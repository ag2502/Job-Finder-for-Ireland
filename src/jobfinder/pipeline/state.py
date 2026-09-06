"""Reconciliation: turning a fetch result into database state.

This module carries the guarantee that the whole product rests on — run the pipeline
today and again tomorrow, and yesterday's still-open roles are still there.

Three rules make that true:

1. **Jobs are never deleted.** A role that disappears is marked closed and keeps its
   history, so it can be reopened with its original `first_seen_at` intact if it
   returns.

2. **A failed crawl never closes anything.** Absence only means something if the source
   was actually reached. If Greenhouse returns a 500, a "replace today's set" design
   would silently delete every Greenhouse job in the database and the user's list would
   collapse overnight. FAILED touches nothing.

3. **A suspiciously small result set is treated as failure.** An upstream change that
   breaks pagination, or a partial outage, returns *some* data and looks successful.
   Comparing against the previous run's count catches the failure mode that a status
   code cannot.

The one-run grace period before closing (`misses_before_close`) trades at most a day of
staleness for immunity to transient flakiness.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.config import settings
from jobfinder.core.models import (
    Company,
    CoverageState,
    CrawlStatus,
    JobPosting,
    JobStatus,
    Source,
    SourceCrawl,
    utcnow,
)
from jobfinder.normalize.dedup import compute_dedup_key, normalize_company_name
from jobfinder.normalize.experience import analyze as analyze_experience
from jobfinder.normalize.location import LocationResult, normalize_location
from jobfinder.normalize.text import html_to_text
from jobfinder.sources.base import FetchResult, RawJob

logger = logging.getLogger(__name__)


@dataclass
class ReconcileStats:
    status: CrawlStatus
    seen: int = 0
    created: int = 0
    updated: int = 0
    closed: int = 0
    reopened: int = 0
    error: str | None = None


def resolve_location(job: RawJob, *, company_is_irish: bool | None = None) -> LocationResult:
    """Resolve a posting's location across its primary and secondary offices.

    A role listed against several offices is a Dublin role if *any* of them is Dublin.
    Checking only the primary field under-counts silently.
    """
    primary = normalize_location(job.location_raw, company_is_irish=company_is_irish)
    if primary.is_dublin:
        return primary

    for extra in job.extra_locations:
        candidate = normalize_location(extra, company_is_irish=company_is_irish)
        if candidate.is_dublin:
            # Keep the raw string the source led with, but adopt the Dublin verdict.
            candidate.raw = job.location_raw
            candidate.is_remote = candidate.is_remote or primary.is_remote
            return candidate

    return primary


def classify(result: FetchResult, prev_jobs_found: int | None) -> CrawlStatus:
    """Apply the volume-drop circuit breaker to an otherwise-successful fetch.

    The breaker deliberately ignores small boards. A company with three openings that
    fills all three is ordinary business, and treating that as suspicious would leave
    its jobs active forever — the breaker would block the very transition it exists to
    make safe. Only a collapse from a baseline large enough to be implausible counts.
    """
    if result.status is not CrawlStatus.OK:
        return result.status

    if prev_jobs_found and prev_jobs_found >= settings.volume_drop_min_baseline:
        ratio = len(result.jobs) / prev_jobs_found
        if ratio < settings.volume_drop_threshold:
            logger.warning(
                "volume drop: %d jobs vs %d previously (%.0f%%) - treating as PARTIAL",
                len(result.jobs),
                prev_jobs_found,
                ratio * 100,
            )
            return CrawlStatus.PARTIAL

    return CrawlStatus.OK


def previous_job_count(session: Session, source: Source) -> int | None:
    stmt = (
        select(SourceCrawl.jobs_found)
        .where(
            SourceCrawl.source_id == source.id,
            SourceCrawl.status == CrawlStatus.OK,
        )
        .order_by(SourceCrawl.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def reconcile(
    session: Session,
    *,
    source: Source,
    company: Company,
    result: FetchResult,
    run_id: int,
) -> ReconcileStats:
    """Fold one source's fetch result into persistent state."""
    prev_count = previous_job_count(session, source)
    status = classify(result, prev_count)
    stats = ReconcileStats(status=status, error=result.error)

    crawl = SourceCrawl(
        run_id=run_id,
        source_id=source.id,
        status=status,
        jobs_found=len(result.jobs),
        prev_jobs_found=prev_count,
        duration_seconds=result.duration_seconds,
        error=result.error,
    )
    session.add(crawl)

    if status is CrawlStatus.FAILED:
        # Rule 2: nothing was learned, so nothing changes. Jobs stay active.
        source.consecutive_failures += 1
        logger.warning(
            "source %s failed (%d consecutive); %s",
            source.slug,
            source.consecutive_failures,
            result.error,
        )
        return stats

    source.consecutive_failures = 0
    source.last_success_at = utcnow()

    seen_ids = _upsert_jobs(session, source=source, company=company, jobs=result.jobs, stats=stats)

    if status is CrawlStatus.OK:
        _close_absent(session, source=source, seen_ids=seen_ids, stats=stats)
    else:
        logger.info(
            "source %s PARTIAL: refreshed %d jobs, skipping absence logic",
            source.slug,
            len(seen_ids),
        )

    return stats


def _upsert_jobs(
    session: Session,
    *,
    source: Source,
    company: Company,
    jobs: list[RawJob],
    stats: ReconcileStats,
) -> set[str]:
    existing = {
        job.source_job_id: job
        for job in session.execute(
            select(JobPosting).where(JobPosting.source_id == source.id)
        ).scalars()
    }

    now = utcnow()
    seen: set[str] = set()
    company_cache: dict[str, Company] = {}

    for raw in jobs:
        if not raw.source_job_id or not raw.title:
            continue
        seen.add(raw.source_job_id)

        # Aggregator sources carry many employers under one source, so the company is a
        # property of the posting rather than of the source.
        job_company = _company_for(session, company, raw, company_cache)

        location = resolve_location(raw)
        description = html_to_text(raw.description)
        experience = analyze_experience(raw.title, description)
        dedup_key = compute_dedup_key(
            job_company.name,
            raw.title,
            is_dublin=location.is_dublin,
            is_remote=location.is_remote,
            location_norm=location.location_norm,
        )

        job = existing.get(raw.source_job_id)
        if job is None:
            session.add(
                JobPosting(
                    company_id=job_company.id,
                    source_id=source.id,
                    source_job_id=raw.source_job_id,
                    dedup_key=dedup_key,
                    title=raw.title,
                    description=description,
                    url=raw.url,
                    location_raw=raw.location_raw,
                    location_norm=location.location_norm,
                    is_dublin=location.is_dublin,
                    is_remote=location.is_remote,
                    needs_location_review=location.needs_review,
                    posted_at=raw.posted_at,
                    min_years_required=experience.min_years,
                    years_inferred=experience.inferred,
                    is_internship=experience.is_internship,
                    is_graduate=experience.is_graduate,
                    first_seen_at=now,
                    last_seen_at=now,
                    status=JobStatus.ACTIVE,
                    consecutive_misses=0,
                )
            )
            stats.created += 1
            continue

        if job.status is JobStatus.CLOSED:
            # A reopened role keeps its original first_seen_at so history stays honest.
            job.status = JobStatus.ACTIVE
            job.closed_at = None
            stats.reopened += 1
        else:
            stats.updated += 1

        job.company_id = job_company.id
        job.title = raw.title
        job.description = description
        job.url = raw.url
        job.location_raw = raw.location_raw
        job.location_norm = location.location_norm
        job.is_dublin = location.is_dublin
        job.is_remote = location.is_remote
        job.needs_location_review = location.needs_review
        job.dedup_key = dedup_key
        job.min_years_required = experience.min_years
        job.years_inferred = experience.inferred
        job.is_internship = experience.is_internship
        job.is_graduate = experience.is_graduate
        if raw.posted_at:
            job.posted_at = raw.posted_at
        job.last_seen_at = now
        job.consecutive_misses = 0

    stats.seen = len(seen)
    return seen


def _company_for(
    session: Session,
    default: Company,
    raw: RawJob,
    cache: dict[str, Company],
) -> Company:
    """Which company a posting belongs to.

    For a company's own board this is always the source's company. Aggregators are the
    exception: one source carries postings from hundreds of employers, so the employer
    named on the posting is resolved — and created if unseen — rather than filing every
    aggregated job under a placeholder. Attributing jobs to the real company is what
    lets `dedup_key` recognise that an aggregated posting and the same role from the
    employer's own board are one job.

    A company created this way enters as UNRESOLVED with no website. Detection only
    probes companies that have one, so these are *not* picked up by the next sweep —
    they sit in the registry as a record that the employer exists and is hiring in
    Dublin, which is itself the useful part: they are the shortlist of employers worth
    adding a website for, and `jobfinder coverage` reports them under the `aggregator`
    origin.
    """
    if not raw.company_name:
        return default

    normalized = normalize_company_name(raw.company_name)
    if not normalized:
        return default

    cached = cache.get(normalized)
    if cached is not None:
        return cached

    company = session.execute(
        select(Company).where(Company.normalized_name == normalized)
    ).scalar_one_or_none()

    if company is None:
        company = Company(
            name=raw.company_name.strip(),
            normalized_name=normalized,
            seed_source="aggregator",
            coverage_state=CoverageState.UNRESOLVED,
            coverage_priority=5,
        )
        session.add(company)
        session.flush()

    cache[normalized] = company
    return company


def _close_absent(
    session: Session,
    *,
    source: Source,
    seen_ids: set[str],
    stats: ReconcileStats,
) -> None:
    """Increment the miss counter for jobs the source did not return, closing those
    that have now been confirmed absent often enough."""
    active = session.execute(
        select(JobPosting).where(
            JobPosting.source_id == source.id,
            JobPosting.status == JobStatus.ACTIVE,
        )
    ).scalars()

    now = utcnow()
    for job in active:
        if job.source_job_id in seen_ids:
            continue

        job.consecutive_misses += 1
        if job.consecutive_misses >= settings.misses_before_close:
            job.status = JobStatus.CLOSED
            job.closed_at = now
            stats.closed += 1
