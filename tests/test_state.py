"""Reconciliation tests.

These cover the product's central promise: yesterday's still-open jobs are still here
today, and no upstream failure can silently erase them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import CrawlStatus, JobPosting, JobStatus
from jobfinder.pipeline.state import classify, reconcile
from jobfinder.sources.base import FetchResult

from conftest import make_job, ok_result


def _jobs(session: Session) -> list[JobPosting]:
    return list(session.execute(select(JobPosting)).scalars())


def _by_id(session: Session, job_id: str) -> JobPosting:
    return session.execute(
        select(JobPosting).where(JobPosting.source_job_id == job_id)
    ).scalar_one()


def _reconcile(session, source, company, run, result):
    return reconcile(
        session, source=source, company=company, result=result, run_id=run.id
    )


# --------------------------------------------------------------------------
# The cumulative guarantee
# --------------------------------------------------------------------------


def test_jobs_persist_across_runs(session, source, company, run):
    """Run twice with the same data; nothing should be closed."""
    _reconcile(session, source, company, run, ok_result("1", "2", "3"))
    session.flush()

    stats = _reconcile(session, source, company, run, ok_result("1", "2", "3"))
    session.flush()

    assert stats.closed == 0
    assert stats.created == 0
    assert stats.updated == 3
    assert all(j.status is JobStatus.ACTIVE for j in _jobs(session))


def test_new_jobs_are_added_without_disturbing_existing(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", "2"))
    session.flush()

    stats = _reconcile(session, source, company, run, ok_result("1", "2", "3"))
    session.flush()

    assert stats.created == 1
    assert stats.closed == 0
    assert len(_jobs(session)) == 3
    assert all(j.status is JobStatus.ACTIVE for j in _jobs(session))


# --------------------------------------------------------------------------
# Closing requires confirmed absence
# --------------------------------------------------------------------------


def test_absent_job_survives_one_miss_then_closes_on_second(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", "2"))
    session.flush()

    # First miss: still active, with the grace period consumed.
    stats = _reconcile(session, source, company, run, ok_result("1"))
    session.flush()
    job2 = _by_id(session, "2")
    assert stats.closed == 0
    assert job2.status is JobStatus.ACTIVE
    assert job2.consecutive_misses == 1

    # Second consecutive miss: now confirmed gone.
    stats = _reconcile(session, source, company, run, ok_result("1"))
    session.flush()
    job2 = _by_id(session, "2")
    assert stats.closed == 1
    assert job2.status is JobStatus.CLOSED
    assert job2.closed_at is not None


def test_reappearing_job_resets_miss_counter(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", "2"))
    _reconcile(session, source, company, run, ok_result("1"))  # miss 1
    session.flush()
    assert _by_id(session, "2").consecutive_misses == 1

    _reconcile(session, source, company, run, ok_result("1", "2"))  # back
    session.flush()
    job2 = _by_id(session, "2")
    assert job2.consecutive_misses == 0
    assert job2.status is JobStatus.ACTIVE


# --------------------------------------------------------------------------
# Rule 2: a failed crawl must never close jobs
# --------------------------------------------------------------------------


def test_failed_crawl_closes_nothing(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", "2", "3"))
    session.flush()
    before = {j.id: j.status for j in _jobs(session)}

    stats = _reconcile(session, source, company, run, FetchResult.failed("HTTP 500"))
    session.flush()

    assert stats.status is CrawlStatus.FAILED
    assert stats.closed == 0
    after = {j.id: j.status for j in _jobs(session)}
    assert after == before
    assert all(s is JobStatus.ACTIVE for s in after.values())


def test_repeated_failures_never_close_jobs(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", "2"))
    session.flush()

    for _ in range(5):
        _reconcile(session, source, company, run, FetchResult.failed("timeout"))
    session.flush()

    assert all(j.status is JobStatus.ACTIVE for j in _jobs(session))
    assert all(j.consecutive_misses == 0 for j in _jobs(session))
    assert source.consecutive_failures == 5


def test_empty_successful_crawl_still_requires_two_misses(session, source, company, run):
    """An empty OK result is meaningful, but still gets the grace period."""
    _reconcile(session, source, company, run, ok_result("1"))
    session.flush()

    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[]))
    session.flush()
    assert _by_id(session, "1").status is JobStatus.ACTIVE

    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[]))
    session.flush()
    assert _by_id(session, "1").status is JobStatus.CLOSED


# --------------------------------------------------------------------------
# Rule 3: the volume-drop circuit breaker
# --------------------------------------------------------------------------


def test_volume_drop_is_downgraded_to_partial(session, source, company, run):
    _reconcile(session, source, company, run, ok_result(*[str(i) for i in range(100)]))
    session.flush()

    # A crawl returning 10% of the previous count looks successful but is not.
    stats = _reconcile(session, source, company, run, ok_result("0", "1"))
    session.flush()

    assert stats.status is CrawlStatus.PARTIAL
    assert stats.closed == 0
    assert sum(1 for j in _jobs(session) if j.status is JobStatus.ACTIVE) == 100


def test_partial_still_refreshes_the_jobs_it_saw(session, source, company, run):
    _reconcile(session, source, company, run, ok_result(*[str(i) for i in range(100)]))
    session.flush()
    _reconcile(session, source, company, run, ok_result("0", "1"))
    session.flush()

    # The two jobs it did return are legitimate and were refreshed.
    assert _by_id(session, "0").consecutive_misses == 0
    assert _by_id(session, "1").consecutive_misses == 0


def test_modest_fluctuation_is_not_treated_as_partial(session, source, company, run):
    _reconcile(session, source, company, run, ok_result(*[str(i) for i in range(10)]))
    session.flush()

    stats = _reconcile(session, source, company, run, ok_result(*[str(i) for i in range(8)]))
    assert stats.status is CrawlStatus.OK


def test_classify_without_history_is_ok():
    assert classify(FetchResult(status=CrawlStatus.OK, jobs=[]), None) is CrawlStatus.OK


def test_small_board_emptying_is_legitimate_not_partial():
    """A three-opening company filling all three is ordinary business.

    If the breaker fired here, small boards could never empty and their jobs would stay
    active forever - the breaker would block the transition it exists to make safe.
    """
    assert classify(FetchResult(status=CrawlStatus.OK, jobs=[]), 3) is CrawlStatus.OK


def test_large_board_collapsing_is_partial():
    assert classify(FetchResult(status=CrawlStatus.OK, jobs=[]), 500) is CrawlStatus.PARTIAL


# --------------------------------------------------------------------------
# Reopening
# --------------------------------------------------------------------------


def test_reopened_job_keeps_original_first_seen(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1"))
    session.flush()
    original_first_seen = _by_id(session, "1").first_seen_at

    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[]))
    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[]))
    session.flush()
    assert _by_id(session, "1").status is JobStatus.CLOSED

    stats = _reconcile(session, source, company, run, ok_result("1"))
    session.flush()

    job = _by_id(session, "1")
    assert stats.reopened == 1
    assert job.status is JobStatus.ACTIVE
    assert job.closed_at is None
    assert job.first_seen_at == original_first_seen


# --------------------------------------------------------------------------
# Location handling during reconciliation
# --------------------------------------------------------------------------


def test_dublin_flag_is_set_from_location(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", location="Dublin, Ireland"))
    session.flush()
    assert _by_id(session, "1").is_dublin


def test_secondary_location_rescues_a_dublin_role(session, source, company, run):
    job = make_job("1", location="London, UK")
    job.extra_locations = ["Dublin, Ireland"]
    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[job]))
    session.flush()

    stored = _by_id(session, "1")
    assert stored.is_dublin, "a Dublin secondary office must not be lost"
    assert stored.location_raw == "London, UK", "raw location must be preserved verbatim"


def test_us_dublin_is_not_flagged(session, source, company, run):
    _reconcile(session, source, company, run, ok_result("1", location="Dublin, CA"))
    session.flush()
    assert not _by_id(session, "1").is_dublin


def test_distinct_roles_with_same_title_are_not_merged(session, source, company, run):
    """Two identical-titled Dublin openings are two jobs, not one."""
    result = FetchResult(
        status=CrawlStatus.OK,
        jobs=[make_job("1"), make_job("2")],  # same title and location
    )
    _reconcile(session, source, company, run, result)
    session.flush()

    jobs = _jobs(session)
    assert len(jobs) == 2
    assert jobs[0].dedup_key == jobs[1].dedup_key  # grouped...
    assert jobs[0].id != jobs[1].id  # ...but not collapsed


def test_a_refresh_without_the_advert_keeps_the_one_already_stored(session, source, company, run):
    """Adapters that fetch adverts separately cap or skip them; the text must not vanish."""
    with_advert = make_job("1")
    with_advert.description = "<p>Build payment systems in Python</p>"
    _reconcile(session, source, company, run, FetchResult(status=CrawlStatus.OK, jobs=[with_advert]))
    session.flush()

    _reconcile(session, source, company, run, ok_result("1"))
    session.flush()

    assert "Build payment systems" in (_by_id(session, "1").description or "")


def test_an_id_repeated_within_one_fetch_does_not_abort_the_run(session, source, company, run):
    """Regression: two Intel postings both carried the id "Spotlight Job".

    The second insert broke the unique constraint, which failed the flush and with it
    every other source's work in the run.
    """
    stats = _reconcile(session, source, company, run, ok_result("1", "1", "2"))
    session.flush()

    assert stats.created == 2
    assert sorted(j.source_job_id for j in _jobs(session)) == ["1", "2"]
