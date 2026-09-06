"""End-to-end pipeline tests through the real orchestrator.

These tests pass `all_sources=True` throughout. Each `crawl()` here stands for a
source's *next scheduled run* — usually "the next day" — whereas the default staleness
filter exists to stop a source being re-fetched seconds after it succeeded. Scheduling
is covered separately in `test_selection.py`; bypassing it here keeps these tests about
reconciliation, which is what they are for.

The headline case is `test_acceptance_yesterdays_jobs_survive_todays_outage`, which is
the product requirement stated directly as a test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import (
    Company,
    CoverageState,
    CrawlStatus,
    JobPosting,
    JobStatus,
    Source,
)
from jobfinder.pipeline.run import CIRCUIT_BREAKER_THRESHOLD, crawl
from jobfinder.sources.base import BaseAdapter, FetchResult, RawJob, register

from conftest import make_job


class ScriptedAdapter(BaseAdapter):
    """Replays a fixed sequence of results, one per crawl."""

    name = "scripted"

    def __init__(self) -> None:
        self.script: list[FetchResult] = []
        self.calls = 0

    def fetch(self, slug: str, client=None) -> FetchResult:  # type: ignore[override]
        result = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return result

    @staticmethod
    def polite_pause() -> None:
        return None


@pytest.fixture
def adapter() -> ScriptedAdapter:
    a = ScriptedAdapter()
    register(a)
    return a


@pytest.fixture
def scripted_source(session: Session) -> Source:
    company = Company(
        name="Scripted Ltd",
        normalized_name="scripted",
        coverage_state=CoverageState.ATS_DETECTED,
    )
    session.add(company)
    session.flush()
    source = Source(company_id=company.id, adapter="scripted", slug="scripted")
    session.add(source)
    session.flush()
    return source


def _ok(*ids: str) -> FetchResult:
    return FetchResult(status=CrawlStatus.OK, jobs=[make_job(i) for i in ids])


def _active_ids(session: Session) -> set[str]:
    return {
        j.source_job_id
        for j in session.execute(
            select(JobPosting).where(JobPosting.status == JobStatus.ACTIVE)
        ).scalars()
    }


def test_acceptance_yesterdays_jobs_survive_todays_outage(
    session, adapter, scripted_source
):
    """The requirement, as a test.

    Crawl today and record the list. Tomorrow the source is down. Every job from today
    must still be present and active - an outage is not evidence that a job closed.
    """
    adapter.script = [
        _ok("a", "b", "c", "d"),          # day 1: healthy
        FetchResult.failed("HTTP 503"),    # day 2: upstream outage
        FetchResult.failed("timeout"),     # day 3: still down
    ]

    crawl(session, all_sources=True)
    session.flush()
    day_one = _active_ids(session)
    assert day_one == {"a", "b", "c", "d"}

    crawl(session, all_sources=True)
    crawl(session, all_sources=True)
    session.flush()

    assert _active_ids(session) == day_one, "an outage must never close jobs"


def test_new_and_existing_jobs_coexist_across_days(session, adapter, scripted_source):
    adapter.script = [
        _ok("a", "b"),
        _ok("a", "b", "c"),  # 'c' appears; a and b continue
    ]

    crawl(session, all_sources=True)
    session.flush()
    day_one = _active_ids(session)

    crawl(session, all_sources=True)
    session.flush()
    day_two = _active_ids(session)

    assert day_one <= day_two, "yesterday's active jobs must all still be active"
    assert day_two - day_one == {"c"}


def test_genuinely_removed_job_closes_after_confirmed_absence(
    session, adapter, scripted_source
):
    adapter.script = [_ok("a", "b"), _ok("a"), _ok("a")]

    crawl(session, all_sources=True)
    crawl(session, all_sources=True)
    crawl(session, all_sources=True)
    session.flush()

    job_b = session.execute(
        select(JobPosting).where(JobPosting.source_job_id == "b")
    ).scalar_one()
    assert job_b.status is JobStatus.CLOSED
    assert job_b.closed_at is not None
    # Closed, never deleted - the row and its history survive.
    assert job_b.first_seen_at is not None


def test_summary_counts_reflect_reality(session, adapter, scripted_source):
    adapter.script = [_ok("a", "b"), _ok("a", "b", "c")]

    first = crawl(session, all_sources=True)
    session.flush()
    assert first.created == 2
    assert first.sources_ok == 1

    second = crawl(session, all_sources=True)
    session.flush()
    assert second.created == 1
    assert second.updated == 2
    assert second.closed == 0


def test_circuit_breaker_disables_a_persistently_failing_source(
    session, adapter, scripted_source
):
    adapter.script = [FetchResult.failed("boom")]

    for _ in range(CIRCUIT_BREAKER_THRESHOLD):
        crawl(session, all_sources=True)
    session.flush()

    assert scripted_source.enabled is False, "a dead source should stop being hammered"


def test_failure_in_one_source_does_not_affect_another(session, adapter, scripted_source):
    """Containment: a broken adapter must not endanger a healthy one's jobs."""
    healthy_company = Company(
        name="Healthy Ltd", normalized_name="healthy", coverage_state=CoverageState.ATS_DETECTED
    )
    session.add(healthy_company)
    session.flush()

    class HealthyAdapter(BaseAdapter):
        name = "healthy"

        def fetch(self, slug, client=None):  # type: ignore[override]
            return FetchResult(status=CrawlStatus.OK, jobs=[make_job("h1"), make_job("h2")])

        @staticmethod
        def polite_pause() -> None:
            return None

    register(HealthyAdapter())
    session.add(Source(company_id=healthy_company.id, adapter="healthy", slug="healthy"))
    session.flush()

    adapter.script = [_ok("a"), FetchResult.failed("broken")]

    crawl(session, all_sources=True)
    session.flush()
    assert _active_ids(session) == {"a", "h1", "h2"}

    summary = crawl(session, all_sources=True)
    session.flush()

    assert summary.sources_failed == 1
    assert summary.sources_ok == 1
    assert _active_ids(session) == {"a", "h1", "h2"}


def test_unknown_adapter_is_recorded_as_failure_not_a_crash(session):
    company = Company(
        name="Ghost Ltd", normalized_name="ghost", coverage_state=CoverageState.ATS_DETECTED
    )
    session.add(company)
    session.flush()
    session.add(Source(company_id=company.id, adapter="does-not-exist", slug="x"))
    session.flush()

    summary = crawl(session, all_sources=True)
    assert summary.sources_failed == 1
