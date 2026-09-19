"""Promoting BLOCKED companies to generic extraction.

`bulk_detect` leaves a company `BLOCKED` when it has a careers page but no readable ATS.
That is the largest group in the registry and always will be: plenty of employers run a
bespoke careers site, and no fingerprint will ever match one.

Many of them still publish `schema.org/JobPosting` markup, because Google requires it for
a role to appear in Google's jobs results. This step finds the ones that do and registers
a `jsonld` source pointed at their careers URL, moving them from `BLOCKED` to
`GENERIC_EXTRACTION`. A page with no markup is then tried with `careers_html`, which reads
a plain vacancy list from the page's own HTML — councils, law firms and retailers whose
careers page is a list of links and nothing more.

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
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder.core.config import settings
from jobfinder.core.models import Company, CoverageState, CrawlStatus, Source, utcnow
from jobfinder.sources.base import build_client, get_adapter
from jobfinder.sources import load_adapters

logger = logging.getLogger(__name__)

# A trial that yields fewer than this is treated as a miss. One stray JobPosting block on
# an "about us" page is not a job board, and registering on the strength of it produces a
# source that reports one phantom role forever.
MIN_TRIAL_JOBS = 2

# Companies between commits; see the note in `registry/bulk_detect.py`.
COMMIT_EVERY = 25

# Generic readers, most reliable first. Structured markup says exactly what a posting
# is; reading a page's own HTML infers it, so it is tried only where there is no markup.
EXTRACTORS = ("jsonld", "careers_html")


@dataclass
class ExtractionStats:
    tried: int = 0
    registered: int = 0
    already_registered: int = 0
    by_adapter: dict[str, int] = field(default_factory=dict)
    empty: int = 0
    failed: int = 0

    def __str__(self) -> str:
        shared = (
            f", {self.already_registered} already covered by a shared page"
            if self.already_registered
            else ""
        )
        adapters = (
            " (" + ", ".join(f"{n} {a}" for a, n in sorted(self.by_adapter.items())) + ")"
            if self.by_adapter
            else ""
        )
        return (
            f"tried {self.tried}: {self.registered} now extractable{adapters}{shared}, "
            f"{self.empty} no usable markup, {self.failed} with no readable vacancy list"
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
    extractors = [(name, get_adapter(name)) for name in EXTRACTORS]
    if any(adapter is None for _, adapter in extractors):  # pragma: no cover
        raise RuntimeError("a generic extractor is not registered")

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
            """The first extractor that reads real postings from the page."""
            company_id, url = target
            # A page that was read but held too little outranks one that could not be
            # read at all, so a miss is reported as the most informative of the two.
            best = None
            for name, adapter in extractors:
                result = adapter.fetch(url, client=client)
                read = result.status is not CrawlStatus.FAILED
                if read and len(result.jobs) >= MIN_TRIAL_JOBS:
                    return company_id, url, name, result
                if best is None or (read and best[1].status is CrawlStatus.FAILED):
                    best = (name, result)
            return company_id, url, best[0], best[1]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(trial, target) for target in targets]
            for future in as_completed(futures):
                company_id, url, adapter_name, result = future.result()
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

                # PARTIAL is a real board read up to the crawl ceiling - exactly the
                # large employers most worth promoting - so only FAILED is a miss.
                if result.status is CrawlStatus.FAILED:
                    stats.failed += 1
                    logger.debug("extraction trial failed for %s: %s", url, result.error)
                    continue

                if len(result.jobs) < MIN_TRIAL_JOBS:
                    stats.empty += 1
                    continue

                # Two companies can share one careers page - a parent and its Irish
                # arm usually do, and detection hands both the same URL. `sources` is
                # unique on (adapter, slug), so a second registration is not merely
                # redundant: it raises IntegrityError on the next flush and takes the
                # whole run down with it. The page is already crawled, so the jobs are
                # not missing; only the duplicate row is.
                existing = session.execute(
                    select(Source).where(Source.adapter == adapter_name, Source.slug == url)
                ).scalars().first()
                if existing is not None:
                    company.coverage_state = CoverageState.GENERIC_EXTRACTION
                    stats.already_registered += 1
                    continue

                if not dry_run:
                    session.add(
                        Source(
                            company_id=company.id,
                            adapter=adapter_name,
                            slug=url,
                            tier=3,
                        )
                    )
                company.coverage_state = CoverageState.GENERIC_EXTRACTION
                stats.registered += 1
                stats.by_adapter[adapter_name] = stats.by_adapter.get(adapter_name, 0) + 1
                logger.info(
                    "%s now extractable via %s: %d jobs from %s",
                    company.name,
                    adapter_name,
                    len(result.jobs),
                    url,
                )

    session.flush()
    return stats
