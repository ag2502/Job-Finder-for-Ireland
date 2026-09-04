"""Crawl orchestration.

Walks the enabled sources, fetches each one, and hands the result to the reconciler.
One source's failure is contained: it is recorded, its jobs are left untouched, and the
crawl moves on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import (
    Company,
    CrawlRun,
    CrawlStatus,
    Source,
    utcnow,
)
from jobfinder.pipeline.state import reconcile
from jobfinder.sources import load_adapters
from jobfinder.sources.base import FetchResult, build_client, get_adapter

logger = logging.getLogger(__name__)

# Disable a source after this many consecutive failures rather than hammering it.
CIRCUIT_BREAKER_THRESHOLD = 10


@dataclass
class RunSummary:
    run_id: int
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


def crawl(session: Session, *, limit: int | None = None, adapter_name: str | None = None) -> RunSummary:
    load_adapters()

    run = CrawlRun()
    session.add(run)
    session.flush()
    summary = RunSummary(run_id=run.id)

    stmt = (
        select(Source, Company)
        .join(Company, Source.company_id == Company.id)
        .where(Source.enabled.is_(True))
        .order_by(Company.coverage_priority, Source.id)
    )
    if adapter_name:
        stmt = stmt.where(Source.adapter == adapter_name)
    if limit:
        stmt = stmt.limit(limit)

    pairs = list(session.execute(stmt).all())
    logger.info("crawling %d sources", len(pairs))

    with build_client() as client:
        for source, company in pairs:
            adapter = get_adapter(source.adapter)
            if adapter is None:
                logger.error("no adapter registered for %r", source.adapter)
                result = FetchResult.failed(f"unknown adapter {source.adapter!r}")
            else:
                logger.info("fetching %s:%s (%s)", source.adapter, source.slug, company.name)
                result = adapter.fetch(source.slug, client=client)
                adapter.polite_pause()

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
