"""Crawl orchestration.

Selects the sources due this run, fetches them concurrently, and hands each result to
the reconciler. One source's failure is contained: it is recorded, its jobs are left
untouched, and the crawl moves on.

Fetching is parallel (see `pipeline/fetcher.py`); reconciliation is not. Every database
write still happens on this thread, one source at a time, so the state machine's
guarantees are exactly what they were when the crawl was serial.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from jobfinder.core.config import settings
from jobfinder.core.models import (
    Company,
    CrawlRun,
    CrawlStatus,
    Source,
    utcnow,
)
from jobfinder.pipeline.fetcher import FetchJob, fetch_all
from jobfinder.pipeline.state import reconcile
from jobfinder.sources import load_adapters

logger = logging.getLogger(__name__)

# Disable a source after this many consecutive failures rather than hammering it.
CIRCUIT_BREAKER_THRESHOLD = 10


@dataclass
class RunSummary:
    run_id: int
    sources_selected: int = 0
    sources_ok: int = 0
    sources_failed: int = 0
    sources_partial: int = 0
    created: int = 0
    updated: int = 0
    closed: int = 0
    reopened: int = 0

    def __str__(self) -> str:
        return (
            f"run {self.run_id}: "
            f"{self.sources_ok} ok / {self.sources_partial} partial / "
            f"{self.sources_failed} failed | "
            f"+{self.created} new, {self.updated} refreshed, "
            f"{self.closed} closed, {self.reopened} reopened"
        )


def select_sources(
    session: Session,
    *,
    limit: int | None = None,
    adapter_name: str | None = None,
    all_sources: bool = False,
) -> list[tuple[Source, Company]]:
    """Choose which sources are due.

    High-priority companies run every time. The long tail runs only once its last
    successful crawl has aged past `stale_after_hours`, which is what keeps a registry
    of thousands inside a six-hour CI window. A source that has never succeeded is
    always due — otherwise a newly-registered company would wait a day for its first
    fetch, and a permanently broken one would never retry.
    """
    stmt = (
        select(Source, Company)
        .join(Company, Source.company_id == Company.id)
        .where(Source.enabled.is_(True))
    )

    if adapter_name:
        stmt = stmt.where(Source.adapter == adapter_name)

    if not all_sources:
        cutoff = utcnow() - timedelta(hours=settings.stale_after_hours)
        stmt = stmt.where(
            or_(
                Company.coverage_priority <= settings.priority_always_crawl,
                Source.last_success_at.is_(None),
                Source.last_success_at < cutoff,
            )
        )

    # Priority first so that if a run is cut short, it is the long tail that is lost.
    stmt = stmt.order_by(Company.coverage_priority, Source.last_success_at.asc().nulls_first(), Source.id)

    if limit:
        stmt = stmt.limit(limit)

    return list(session.execute(stmt).all())


def crawl(
    session: Session,
    *,
    limit: int | None = None,
    adapter_name: str | None = None,
    all_sources: bool = False,
    max_workers: int | None = None,
) -> RunSummary:
    load_adapters()

    run = CrawlRun()
    session.add(run)
    session.flush()
    summary = RunSummary(run_id=run.id)

    pairs = select_sources(
        session, limit=limit, adapter_name=adapter_name, all_sources=all_sources
    )
    summary.sources_selected = len(pairs)
    logger.info(
        "crawling %d sources with %d workers",
        len(pairs),
        max_workers or settings.crawl_max_workers,
    )

    fetch_jobs = [FetchJob(source=source, company=company) for source, company in pairs]

    for fetch_job, result in fetch_all(fetch_jobs, max_workers=max_workers):
        source, company = fetch_job.source, fetch_job.company

        stats = reconcile(
            session, source=source, company=company, result=result, run_id=run.id
        )

        if stats.status is CrawlStatus.OK:
            summary.sources_ok += 1
        elif stats.status is CrawlStatus.PARTIAL:
            summary.sources_partial += 1
        else:
            summary.sources_failed += 1

        summary.created += stats.created
        summary.updated += stats.updated
        summary.closed += stats.closed
        summary.reopened += stats.reopened

        if source.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            source.enabled = False
            logger.error(
                "disabling %s:%s after %d consecutive failures",
                source.adapter,
                source.slug,
                source.consecutive_failures,
            )

        session.flush()

    run.finished_at = utcnow()
    run.sources_ok = summary.sources_ok
    run.sources_failed = summary.sources_failed
    run.sources_partial = summary.sources_partial
    run.jobs_created = summary.created
    run.jobs_updated = summary.updated
    run.jobs_closed = summary.closed
    run.jobs_reopened = summary.reopened

    return summary
