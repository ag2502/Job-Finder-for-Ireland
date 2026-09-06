"""Source scheduling and the concurrent fetcher's politeness guarantees.

Scaling the registry from tens of companies to thousands makes two things load-bearing
that were previously irrelevant: which sources are due on a given run, and how much
simultaneous traffic one host receives.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from jobfinder.core.config import settings
from jobfinder.core.models import Company, CoverageState, CrawlStatus, Source, utcnow
from jobfinder.pipeline.fetcher import FetchJob, HostLimiter, fetch_all, host_for
from jobfinder.pipeline.run import select_sources
from jobfinder.sources.base import BaseAdapter, FetchResult, register


def _company(session: Session, name: str, priority: int) -> Company:
    company = Company(
        name=name,
        normalized_name=name.lower().replace(" ", ""),
        coverage_priority=priority,
        coverage_state=CoverageState.ATS_DETECTED,
    )
    session.add(company)
    session.flush()
    return company


def _source(session: Session, company: Company, slug: str, *, last_success) -> Source:
    source = Source(
        company_id=company.id,
        adapter="greenhouse",
        slug=slug,
        tier=1,
        enabled=True,
        last_success_at=last_success,
    )
    session.add(source)
    session.flush()
    return source


# ---------------------------------------------------------------------------
# Which sources are due
# ---------------------------------------------------------------------------


def test_high_priority_runs_every_time_and_the_long_tail_waits(session: Session):
    """The multinationals carry most of the roles, so they never wait for staleness."""
    fresh = utcnow()

    big = _company(session, "Big MNC", priority=1)
    _source(session, big, "bigmnc", last_success=fresh)

    small = _company(session, "Small Consultancy", priority=5)
    _source(session, small, "smallco", last_success=fresh)

    slugs = {source.slug for source, _ in select_sources(session)}
    assert slugs == {"bigmnc"}, "a just-crawled long-tail source is not due again"


def test_long_tail_becomes_due_once_it_goes_stale(session: Session):
    stale = utcnow() - timedelta(hours=settings.stale_after_hours + 1)

    small = _company(session, "Small Consultancy", priority=5)
    _source(session, small, "smallco", last_success=stale)

    slugs = {source.slug for source, _ in select_sources(session)}
    assert slugs == {"smallco"}


def test_a_source_that_has_never_succeeded_is_always_due(session: Session):
    """Otherwise a newly-registered company waits a day for its first fetch, and a
    permanently broken one would never be retried at all."""
    new = _company(session, "Just Added", priority=5)
    _source(session, new, "justadded", last_success=None)

    slugs = {source.slug for source, _ in select_sources(session)}
    assert slugs == {"justadded"}


def test_all_sources_overrides_the_schedule(session: Session):
    fresh = utcnow()
    small = _company(session, "Small Consultancy", priority=5)
    _source(session, small, "smallco", last_success=fresh)

    assert select_sources(session) == []
    assert len(select_sources(session, all_sources=True)) == 1


def test_priority_leads_so_a_truncated_run_loses_only_the_long_tail(session: Session):
    """`--limit` and a CI timeout cut from the same end. The tail is the cheap end."""
    stale = utcnow() - timedelta(days=2)

    tail = _company(session, "Tail Co", priority=5)
    _source(session, tail, "tailco", last_success=stale)
    top = _company(session, "Top Co", priority=1)
    _source(session, top, "topco", last_success=stale)

    ordered = [source.slug for source, _ in select_sources(session)]
    assert ordered[0] == "topco"

    assert [s.slug for s, _ in select_sources(session, limit=1)] == ["topco"]


def test_disabled_sources_are_never_selected(session: Session):
    company = _company(session, "Broken Co", priority=1)
    source = _source(session, company, "brokenco", last_success=None)
    source.enabled = False
    session.flush()

    assert select_sources(session) == []


# ---------------------------------------------------------------------------
# Host attribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter,slug,expected",
    [
        # Platforms where every customer shares one API endpoint: this is the case that
        # needs rate limiting, because twenty slugs are twenty requests to one host.
        ("greenhouse", "stripe", "boards-api.greenhouse.io"),
        ("greenhouse", "intercom", "boards-api.greenhouse.io"),
        ("smartrecruiters", "BoschGroup", "api.smartrecruiters.com"),
        # Aggregator sources are many queries against one API, so they share a slot.
        ("adzuna", "dublin", "api.adzuna.com"),
        ("adzuna", "dublin:data engineer", "api.adzuna.com"),
        # Platforms where the slug *is* the host: separate customers are separate
        # infrastructure and must not share a rate-limit slot.
        ("recruitee", "nmbrs", "nmbrs.recruitee.com"),
        ("personio", "acme", "acme.jobs.personio.de"),
        ("personio", "acme:com", "acme.jobs.personio.de"),
        ("workday", "accenture:wd103:AccentureCareers", "accenture.wd103.myworkdayjobs.com"),
    ],
)
def test_host_attribution(adapter: str, slug: str, expected: str):
    assert host_for(adapter, slug) == expected


def test_shared_platform_slugs_collapse_to_one_host():
    """The property that makes the limiter work at all."""
    assert host_for("greenhouse", "a") == host_for("greenhouse", "b")
    assert host_for("recruitee", "a") != host_for("recruitee", "b")


# ---------------------------------------------------------------------------
# Politeness under concurrency
# ---------------------------------------------------------------------------


def test_limiter_serialises_one_host_but_not_the_crawl():
    """Two requests to one host never overlap; two hosts proceed together.

    This is the whole reason concurrency is per host rather than global — a pool of
    sixteen workers must not become sixteen simultaneous requests to Greenhouse.
    """
    limiter = HostLimiter(delay_seconds=0)
    in_flight: dict[str, int] = {"same": 0, "other": 0}
    peak: dict[str, int] = {"same": 0, "other": 0}
    lock = threading.Lock()
    started = threading.Barrier(4)

    def work(host: str, key: str):
        def body():
            with lock:
                in_flight[key] += 1
                peak[key] = max(peak[key], in_flight[key])
            time.sleep(0.05)
            with lock:
                in_flight[key] -= 1

        started.wait(timeout=5)
        limiter.run(host, body)

    threads = [
        threading.Thread(target=work, args=("one.example", "same")),
        threading.Thread(target=work, args=("one.example", "same")),
        threading.Thread(target=work, args=("two.example", "other")),
        threading.Thread(target=work, args=("three.example", "other")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert peak["same"] == 1, "one host must never see two in-flight requests"
    assert peak["other"] == 2, "different hosts must overlap, or there is no speed-up"


def test_limiter_enforces_the_courtesy_delay_between_calls_to_one_host():
    limiter = HostLimiter(delay_seconds=0.1)
    started = time.monotonic()
    for _ in range(3):
        limiter.run("one.example", lambda: None)
    # Three calls means two gaps; the first is not delayed.
    assert time.monotonic() - started >= 0.2


# ---------------------------------------------------------------------------
# Fetching concurrently
# ---------------------------------------------------------------------------


class _CountingAdapter(BaseAdapter):
    name = "counting"

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, slug: str, client=None) -> FetchResult:  # type: ignore[override]
        with self._lock:
            self.seen.append(slug)
        return FetchResult(status=CrawlStatus.OK, jobs=[])


class _CrashingAdapter(BaseAdapter):
    name = "crashing"

    def fetch(self, slug: str, client=None) -> FetchResult:  # type: ignore[override]
        raise RuntimeError("worker exploded")


def test_every_source_is_fetched_exactly_once(session: Session):
    adapter = _CountingAdapter()
    register(adapter)

    company = _company(session, "Many Boards", priority=1)
    jobs = []
    for i in range(12):
        source = Source(
            company_id=company.id, adapter="counting", slug=f"slug{i}", tier=1
        )
        session.add(source)
        session.flush()
        jobs.append(FetchJob(source=source, company=company))

    results = list(fetch_all(jobs, max_workers=4))

    assert len(results) == 12
    assert sorted(adapter.seen) == sorted(f"slug{i}" for i in range(12))


def test_a_crashing_worker_becomes_a_failed_result_not_a_dead_crawl(session: Session):
    """A pool that propagates an exception would abandon every source behind it."""
    register(_CrashingAdapter())

    company = _company(session, "Explodes", priority=1)
    source = Source(company_id=company.id, adapter="crashing", slug="boom", tier=1)
    session.add(source)
    session.flush()

    (_, result), = list(fetch_all([FetchJob(source=source, company=company)]))

    assert result.status is CrawlStatus.FAILED
    assert "worker exploded" in (result.error or "")


def test_unknown_adapter_fails_that_source_alone(session: Session):
    company = _company(session, "Ghost", priority=1)
    source = Source(company_id=company.id, adapter="nosuchadapter", slug="x", tier=1)
    session.add(source)
    session.flush()

    (_, result), = list(fetch_all([FetchJob(source=source, company=company)]))

    assert result.status is CrawlStatus.FAILED
    assert "unknown adapter" in (result.error or "")
