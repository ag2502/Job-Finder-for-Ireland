"""Promoting BLOCKED companies to generic extraction.

`bulk_detect` leaves a company `BLOCKED` when it has a careers page but no readable ATS.
That is the largest group in the registry and always will be: plenty of employers run a
bespoke careers site, and no fingerprint will ever match one.

Many of them still publish `schema.org/JobPosting` markup, because Google requires it for
a role to appear in Google's jobs results. This step finds the ones that do and registers
a `jsonld` source pointed at their careers URL, moving them from `BLOCKED` to
`GENERIC_EXTRACTION`.

## Why it probes before registering

A `jsonld` source is a crawler aimed at somebody's website, not an API call. Registering
one speculatively for every blocked company would add hundreds of sources that fail on
every run until the circuit breaker disables them — noise in the crawl, pointless traffic
to sites that have nothing to give, and a coverage number that claims companies it cannot
actually read.

So each candidate is extracted from once, for real, and only registered if that trial
returns at least one posting. Coverage then means what it says.

This is deliberately a separate command from `detect-all`. Detection is cheap enough to
run over the whole registry; a trial extraction is a small crawl of an unknown site, and
it should be an explicit decision rather than a side effect.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder.core.config import settings
from jobfinder.core.models import Company, CoverageState, Source, utcnow
from jobfinder.sources.base import build_client, get_adapter
from jobfinder.sources import load_adapters

logger = logging.getLogger(__name__)

# A trial that yields fewer than this is treated as a miss. One stray JobPosting block on
# an "about us" page is not a job board, and registering on the strength of it produces a
# source that reports one phantom role forever.
MIN_TRIAL_JOBS = 2

# Companies between commits; see the note in `registry/bulk_detect.py`.
COMMIT_EVERY = 25


@dataclass
class ExtractionStats:
    tried: int = 0
    registered: int = 0
    empty: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"tried {self.tried}: {self.registered} now extractable, "
            f"{self.empty} no usable markup, {self.failed} unreachable or disallowed"
        )


def blocked_candidates(session: Session, *, limit: int | None = None) -> list[Company]:
    """Companies with a careers page, no ATS, and no source yet."""
    has_source = (
        select(func.count())
        .select_from(Source)
        .where(Source.company_id == Company.id, Source.enabled.is_(True))
        .correlate(Company)
        .scalar_subquery()
    )

    stmt = (
        select(Company)
        .where(
            Company.coverage_state == CoverageState.BLOCKED,
            Company.careers_url.is_not(None),
            has_source == 0,
        )
        .order_by(Company.coverage_priority, Company.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def promote_blocked(
    session: Session,
    *,
    limit: int | None = None,
    max_workers: int | None = None,
    dry_run: bool = False,
) -> ExtractionStats:
    """Trial-extract each blocked company and register the ones that work."""
    load_adapters()
    adapter = get_adapter("jsonld")
    if adapter is None:  # pragma: no cover - registration is unconditional
        raise RuntimeError("jsonld adapter is not registered")

    companies = blocked_candidates(session, limit=limit)
    stats = ExtractionStats()
    if not companies:
        return stats

    max_workers = max_workers or settings.detect_max_workers
    logger.info("trial-extracting %d blocked companies", len(companies))

    # Only primitives cross the thread boundary; a Session-bound attribute read from a
    # worker can trigger a lazy load on the wrong thread.
    targets = [(c.id, c.careers_url or "") for c in companies]
    by_id = {c.id: c for c in companies}

    with build_client() as client:
        def trial(target: tuple[int, str]):
            company_id, url = target
            return company_id, url, adapter.fetch(url, client=client)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(trial, target) for target in targets]
            for future in as_completed(futures):
                company_id, url, result = future.result()
                stats.tried += 1
                company = by_id[company_id]
                company.detection_checked_at = utcnow()

                # Committed in batches for the same reason as the detection sweep: this
                # runs long enough that a pooled connection can be closed underneath it.
                # Placed before the branches below, which all `continue` — a batch of
                # nothing but failures still advances `detection_checked_at`, and that
                # progress is worth keeping too.
                if stats.tried % COMMIT_EVERY == 0:
                    session.commit()

                if not result.ok:
                    stats.failed += 1
                    logger.debug("extraction trial failed for %s: %s", url, result.error)
                    continue

                if len(result.jobs) < MIN_TRIAL_JOBS:
                    stats.empty += 1
                    continue

                if not dry_run:
                    session.add(
                        Source(
                            company_id=company.id,
                            adapter="jsonld",
                            slug=url,
                            tier=3,
                        )
                    )
                company.coverage_state = CoverageState.GENERIC_EXTRACTION
                stats.registered += 1
                logger.info(
                    "%s now extractable: %d jobs from %s",
                    company.name,
                    len(result.jobs),
                    url,
                )

    session.flush()
    return stats
