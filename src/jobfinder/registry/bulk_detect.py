"""Running ATS detection across the whole registry.

This is the step that turns a list of Irish employers into crawlable sources. It is the
highest-leverage thing in the project: coverage is `registry size x detection hit rate`,
and a registry of thousands with no detection sweep crawls nothing at all.

Like the crawl, detection is parallel over the network and serial at the database. Each
company is probed on a worker thread; the results come back to the caller's thread to be
written one at a time. Detection touches `companies` and `sources` rather than
`job_postings`, so it cannot affect the reconciler's guarantees either way — but a
Session is still not thread-safe, and the uniqueness checks below need to see each
other's writes.

Every outcome is recorded, including failure. `coverage_state` is the project's answer to
"did we miss anyone?", and it is only an answer if the misses are written down:

* `ATS_DETECTED`     - a Tier 1 source was registered and will be crawled
* `BLOCKED`          - a careers page exists but no ATS could be read from it
* `NO_CAREERS_PAGE`  - the site was reached and has no careers page to find
* `UNRESOLVED`       - the site could not be reached at all; worth retrying

`BLOCKED` is not a failure state so much as a work queue. Those companies keep their
`careers_url`, which is what the portal links to so they appear in the directory rather
than disappearing.
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError

from jobfinder.core.config import settings
from jobfinder.core.models import Company, CoverageState, Source, utcnow
from jobfinder.registry.detect import Detection, detect_for_website
from jobfinder.sources.base import build_client

logger = logging.getLogger(__name__)

# Adapters whose slug cannot be trusted straight from a page fingerprint, mapped to the
# validator that settles it. SmartRecruiters answers an unknown tenant with HTTP 200 and
# an empty list, so a bad slug would otherwise register as a healthy source reporting
# zero jobs forever. See `sources/smartrecruiters.py`.
def _verify_smartrecruiters(slug: str, client: httpx.Client) -> bool:
    from jobfinder.sources.smartrecruiters import verify_slug

    return verify_slug(slug, client=client)


def _verify_by_fetch(adapter_name: str):
    """A validator that accepts a slug only if a real fetch returns postings.

    Detection's fingerprints for these platforms predate their adapters and capture what
    the page shows, which is not always what the adapter addresses: an Oracle pod name
    without its site number, a SuccessFactors company id rather than the careers host.
    Registering those would add sources that fail every crawl until the breaker trips. A
    populated fetch is proof the slug works; anything less is refused.
    """

    def verify(slug: str, client: httpx.Client | None) -> bool:
        from jobfinder.core.models import CrawlStatus
        from jobfinder.sources.base import get_adapter

        adapter = get_adapter(adapter_name)
        if adapter is None:
            return False
        result = adapter.fetch(slug, client=client)
        return result.status is not CrawlStatus.FAILED and bool(result.jobs)

    return verify


SLUG_VALIDATORS = {
    "smartrecruiters": _verify_smartrecruiters,
    **{
        name: _verify_by_fetch(name)
        for name in ("bamboohr", "icims", "oracle_recruiting", "successfactors", "teamtailor")
    },
}

# How many companies to process between commits. Small enough that little is lost to a
# dropped connection, large enough that the write cost stays negligible.
COMMIT_EVERY = 25

# Adapters this project can actually crawl. Detection recognises more platforms than it
# has adapters for — that is deliberate, because knowing a company is on Teamtailor is
# worth recording — but registering a source for a platform with no adapter would
# produce a source that fails every run and eventually trips the circuit breaker.
from jobfinder.sources import load_adapters  # noqa: E402
from jobfinder.sources.base import all_adapters  # noqa: E402


@dataclass
class SweepStats:
    checked: int = 0
    detected: int = 0
    registered: int = 0
    blocked: int = 0
    no_careers_page: int = 0
    unreachable: int = 0
    already_registered: int = 0
    no_adapter: int = 0
    rejected_slug: int = 0
    write_failed: int = 0
    by_adapter: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        adapters = ", ".join(
            f"{name} {count}" for name, count in sorted(
                self.by_adapter.items(), key=lambda kv: -kv[1]
            )
        )
        failed = f" | {self.write_failed} skipped (db unreachable)" if self.write_failed else ""
        return (
            f"checked {self.checked}: {self.detected} ATS found, "
            f"{self.registered} newly registered "
            f"({self.already_registered} already known, {self.no_adapter} no adapter, "
            f"{self.rejected_slug} slug rejected) | "
            f"{self.blocked} blocked, {self.no_careers_page} no careers page, "
            f"{self.unreachable} unreachable"
            + failed
            + (f"\n  {adapters}" if adapters else "")
        )


def pending_companies(
    session: Session,
    *,
    limit: int | None = None,
    recheck_after_days: int = 30,
    include_detected: bool = False,
) -> list[Company]:
    """Companies worth probing.

    A company that already has an enabled source is skipped — it is being crawled, and
    re-detecting it would only risk replacing a working source with a worse guess.
    Everything else is retried once its last check has aged out, because sites get
    rebuilt and a company that had no careers page last quarter may have one now.
    """
    cutoff = utcnow() - timedelta(days=recheck_after_days)

    has_source = (
        select(func.count())
        .select_from(Source)
        .where(Source.company_id == Company.id, Source.enabled.is_(True))
        .correlate(Company)
        .scalar_subquery()
    )

    stmt = select(Company).where(Company.website.is_not(None))
    if not include_detected:
        stmt = stmt.where(has_source == 0)
    stmt = stmt.where(
        or_(
            Company.detection_checked_at.is_(None),
            Company.detection_checked_at < cutoff,
        )
    )
    # Highest-value companies first, so a truncated sweep loses the least.
    stmt = stmt.order_by(Company.coverage_priority, Company.id)
    if limit:
        stmt = stmt.limit(limit)

    return list(session.execute(stmt).scalars())


def _probe_all(
    companies: list[Company],
    *,
    max_workers: int,
    client: httpx.Client,
    deadline_seconds: float,
    abandoned: threading.Event,
) -> Iterator[tuple[int, str, Detection | Exception]]:
    """Detect across a pool of daemon threads, yielding (company id, name, outcome).

    Only primitives cross the thread boundary. Touching a Session-bound attribute from a
    worker can trigger a lazy load on another thread, which is the kind of race that
    fails once a week and never in a test.

    **Why hand-rolled daemon threads rather than ThreadPoolExecutor.** A sweep visits
    hundreds of unknown hosts, and some fraction of them will hang in a way no HTTP
    timeout covers — name resolution most notoriously, since `getaddrinfo` is a blocking
    C call that ignores both `socket.setdefaulttimeout` and httpx's timeouts. A worker
    stuck there cannot be cancelled or interrupted, and `ThreadPoolExecutor.__exit__`
    *joins* its workers, so one wedged lookup stops the entire sweep at the point where
    it tries to finish. Three consecutive full sweeps of this registry died exactly that
    way, each after several hundred companies.

    The fix is not to interrupt the stuck worker — that is not possible — but to stop
    waiting on it. Daemon threads can be abandoned without blocking interpreter exit, so
    the sweep runs to an overall deadline and returns what it has. The companies whose
    probes never came back keep their previous state and are retried next run, which is
    exactly how an unreachable site is meant to be treated.
    """
    targets: queue.Queue[tuple[int, str, str] | None] = queue.Queue()
    results: queue.Queue[tuple[int, str, Detection | Exception]] = queue.Queue()

    for company in companies:
        targets.put((company.id, company.name, company.website or ""))

    def worker() -> None:
        while True:
            try:
                target = targets.get_nowait()
            except queue.Empty:
                return
            if target is None:
                return
            company_id, name, website = target
            try:
                outcome: Detection | Exception = detect_for_website(
                    website, client=client, name=name
                )
            except Exception as exc:  # noqa: BLE001 - one bad site must not stop the sweep
                logger.debug("detection crashed for %s: %r", website, exc)
                outcome = exc
            results.put((company_id, name, outcome))

    threads = [
        threading.Thread(target=worker, daemon=True, name=f"detect-{i}")
        for i in range(min(max_workers, len(companies)))
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + deadline_seconds
    for completed in range(len(companies)):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            abandoned.set()
            logger.warning(
                "detection deadline reached; abandoning %d unfinished probes",
                len(companies) - completed,
            )
            return
        try:
            yield results.get(timeout=remaining)
        except queue.Empty:
            abandoned.set()
            logger.warning("detection deadline reached while waiting for a probe")
            return


def sweep(
    session: Session,
    *,
    limit: int | None = None,
    max_workers: int | None = None,
    recheck_after_days: int = 30,
    dry_run: bool = False,
    deadline_seconds: float | None = None,
) -> SweepStats:
    """Detect and register sources for every company that does not have one."""
    load_adapters()
    known_adapters = set(all_adapters())

    companies = pending_companies(
        session, limit=limit, recheck_after_days=recheck_after_days
    )
    stats = SweepStats()
    if not companies:
        return stats

    max_workers = max_workers or settings.detect_max_workers
    logger.info("detecting across %d companies with %d workers", len(companies), max_workers)

    by_id = {company.id: company for company in companies}

    # Not a `with` block. If the deadline fires, workers are still inside a blocking
    # call holding pooled connections, and `Client.close()` waits on that pool — so
    # closing would reintroduce at teardown exactly the hang the deadline just escaped.
    # The client is closed only on a clean finish; otherwise it is left to the process,
    # which is about to exit anyway.
    abandoned = threading.Event()
    client = build_client()

    # Probe everything first, touching no database at all; write once at the end.
    #
    # Three earlier designs failed against hosted Postgres, each for the same underlying
    # reason: the connection was alive across the *probing*, which takes tens of minutes,
    # and Neon's free tier suspends an idle compute and drops its connections. Writing
    # per result held a transaction open the whole sweep. Writing per batch of 25 still
    # left ten-minute gaps between commits. Neither is survivable by retrying, because a
    # SELECT inside `_apply` triggers autoflush of the pending writes, so the failure
    # lands mid-query rather than at a commit boundary where it could be caught cleanly.
    #
    # Separating the phases removes the problem rather than defending against it. During
    # probing there is no connection to lose. The write is then a few seconds of work,
    # short enough that a suspended compute simply wakes for it, and short enough that a
    # single retry genuinely fixes a dropped link instead of racing the next drop.
    #
    # The cost is that a crash mid-probe loses the sweep's findings. That is acceptable:
    # detection is idempotent and re-running costs time, not correctness.
    results: list[tuple[int, Detection | Exception]] = []

    try:
        for company_id, _name, outcome in _probe_all(
            companies,
            max_workers=max_workers,
            client=client,
            deadline_seconds=deadline_seconds or settings.detect_sweep_deadline_seconds,
            abandoned=abandoned,
        ):
            results.append((company_id, outcome))
            if len(results) % 50 == 0:
                logger.info("… %d of %d probed", len(results), len(companies))
    finally:
        if not abandoned.is_set():
            client.close()

    logger.info("probing done (%d results); writing to the database", len(results))
    for offset in range(0, len(results), COMMIT_EVERY):
        _apply_batch(
            session,
            results[offset : offset + COMMIT_EVERY],
            by_id=by_id,
            stats=stats,
            known_adapters=known_adapters,
            client=None,
            dry_run=dry_run,
        )
    logger.info("wrote %d results, %d registered", stats.checked, stats.registered)
    return stats


def _apply_batch(
    session: Session,
    batch: list[tuple[int, "Detection | Exception"]],
    *,
    by_id: dict[int, Company],
    stats: SweepStats,
    known_adapters: set[str],
    client: httpx.Client | None,
    dry_run: bool,
) -> None:
    """Apply one batch of probe results and commit, retrying once on a dropped link.

    The retry exists because the failure it handles is expected rather than
    exceptional: a pooled connection that has gone stale announces itself only when the
    next statement is sent. `rollback()` returns the dead connection to the pool, where
    pre-ping discards it, so the second attempt runs on a fresh one. Retrying twice
    would be papering over a real outage; retrying once turns a routine reconnect into a
    non-event.
    """
    for attempt in (1, 2):
        # Every counter is restored before a retry, not just `checked`. A batch that
        # fails halfway has already incremented `registered`, `blocked` and the
        # per-adapter tally for the rows it got through, and replaying it would count
        # those twice — quietly inflating the very numbers the coverage report exists to
        # be trusted on.
        snapshot = copy.deepcopy(stats.__dict__)
        try:
            for company_id, outcome in batch:
                company = by_id[company_id]
                stats.checked += 1

                if isinstance(outcome, Exception):
                    stats.unreachable += 1
                    company.coverage_state = CoverageState.UNRESOLVED
                    company.detection_checked_at = utcnow()
                    continue

                _apply(
                    session,
                    company=company,
                    detection=outcome,
                    stats=stats,
                    known_adapters=known_adapters,
                    client=client,
                    dry_run=dry_run,
                )
            session.commit()
            return
        except (OperationalError, DetachedInstanceError):
            # `invalidate`, not just `rollback`. Rollback hands the connection back to
            # the pool, and a connection the server has already closed can be handed
            # straight back out — pre-ping only checks connections it is asked for, and
            # a mid-transaction failure never releases it cleanly. Invalidating discards
            # it outright, so the retry is guaranteed a new one.
            #
            # It also expunges every object the session knows about, which detaches the
            # `Company` rows this batch is holding in `by_id`. Reusing those same
            # detached instances on retry raises `DetachedInstanceError` the moment an
            # unloaded attribute is touched (`company.id` in `_apply`) - hence that
            # exception is caught here too, and `by_id` is refreshed with session-
            # attached rows before the retry runs.
            #
            # None of this is guaranteed to work: a connection that keeps dropping (a
            # burst of drops from the same underlying outage) can detach the very
            # objects this handler just re-fetched, or fail the re-fetch itself. A
            # sweep that raises here loses every batch queued after it - including
            # results from companies that were probed successfully over the network
            # and only failed to *write* - so a batch that still cannot land after one
            # retry is logged and skipped rather than fatal. Its companies simply keep
            # their prior coverage_state and are picked up by the next sweep.
            session.invalidate()
            stats.__dict__.update(copy.deepcopy(snapshot))
            if attempt == 2:
                stats.write_failed += len(batch)
                logger.error(
                    "database connection would not recover; skipping %d companies "
                    "this run (they keep their prior state and are retried next sweep)",
                    len(batch),
                )
                return
            try:
                ids = [company_id for company_id, _ in batch]
                by_id.update(
                    {
                        company.id: company
                        for company in session.execute(
                            select(Company).where(Company.id.in_(ids))
                        ).scalars()
                    }
                )
            except OperationalError:
                stats.write_failed += len(batch)
                logger.error(
                    "database connection would not recover; skipping %d companies "
                    "this run (they keep their prior state and are retried next sweep)",
                    len(batch),
                )
                return
            logger.warning("database connection lost; retrying this batch")


def _apply(
    session: Session,
    *,
    company: Company,
    detection: Detection,
    stats: SweepStats,
    known_adapters: set[str],
    client: httpx.Client,
    dry_run: bool,
) -> None:
    company.detection_checked_at = utcnow()
    if detection.careers_url:
        company.careers_url = detection.careers_url

    if not detection.found:
        if detection.careers_url and "careers page found" in (detection.note or ""):
            company.coverage_state = CoverageState.BLOCKED
            stats.blocked += 1
        elif detection.note and detection.note.startswith("fetch failed"):
            company.coverage_state = CoverageState.UNRESOLVED
            stats.unreachable += 1
        else:
            company.coverage_state = CoverageState.NO_CAREERS_PAGE
            stats.no_careers_page += 1
        return

    adapter, slug = detection.adapter, detection.slug
    assert adapter and slug  # guaranteed by Detection.found
    stats.detected += 1
    stats.by_adapter[adapter] = stats.by_adapter.get(adapter, 0) + 1

    if adapter not in known_adapters:
        # Recognised platform, no adapter yet. Recorded rather than registered: a source
        # with no adapter fails every crawl until the circuit breaker disables it.
        company.coverage_state = CoverageState.BLOCKED
        stats.no_adapter += 1
        logger.info("%s uses %s (%s) - no adapter yet", company.name, adapter, slug)
        return

    validator = SLUG_VALIDATORS.get(adapter)
    if validator and not validator(slug, client):
        company.coverage_state = CoverageState.BLOCKED
        stats.rejected_slug += 1
        logger.info("%s: rejected %s slug %r (no postings)", company.name, adapter, slug)
        return

    existing = session.execute(
        select(Source).where(Source.adapter == adapter, Source.slug == slug)
    ).scalar_one_or_none()
    if existing is not None:
        # Two companies can legitimately resolve to one board — a parent and its Irish
        # subsidiary usually share it. The board is already crawled, so the jobs are not
        # missing; only the second registration is redundant.
        stats.already_registered += 1
        company.coverage_state = CoverageState.ATS_DETECTED
        return

    if not dry_run:
        session.add(Source(company_id=company.id, adapter=adapter, slug=slug, tier=1))
    company.coverage_state = CoverageState.ATS_DETECTED
    stats.registered += 1
    logger.info("registered %s -> %s:%s", company.name, adapter, slug)
