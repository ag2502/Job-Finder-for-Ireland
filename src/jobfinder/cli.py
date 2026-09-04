"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select

from jobfinder.core.config import settings
from jobfinder.core.db import init_db, session_scope
from jobfinder.core.models import (
    Company,
    CoverageState,
    CrawlRun,
    CrawlStatus,
    JobPosting,
    JobStatus,
    Source,
    SourceCrawl,
)
from jobfinder.pipeline.run import crawl
from jobfinder.registry.seed import seed_companies


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else getattr(logging, settings.log_level),
        format="%(levelname)-8s %(name)s: %(message)s",
    )


def cmd_init_db(args: argparse.Namespace) -> int:
    init_db()
    print(f"database ready at {settings.database_url}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        companies, sources = seed_companies(session)
    print(f"seeded {companies} new companies and {sources} new sources")
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    init_db()
    with session_scope() as session:
        summary = crawl(session, limit=args.limit, adapter_name=args.adapter)
    print(summary)

    # The learned vocabulary is derived from the adverts, so it goes stale the moment
    # the corpus changes. Rebuilding here keeps it in step with every crawl.
    from jobfinder.matching import vocabulary

    vocab = vocabulary.refresh()
    print(f"vocabulary rebuilt: {vocab.size:,} terms from {vocab.n_docs:,} adverts")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with session_scope() as session:
        companies = session.scalar(select(func.count()).select_from(Company)) or 0
        sources = session.scalar(select(func.count()).select_from(Source)) or 0
        active = (
            session.scalar(
                select(func.count())
                .select_from(JobPosting)
                .where(JobPosting.status == JobStatus.ACTIVE)
            )
            or 0
        )
        dublin = (
            session.scalar(
                select(func.count())
                .select_from(JobPosting)
                .where(
                    JobPosting.status == JobStatus.ACTIVE,
                    JobPosting.is_dublin.is_(True),
                )
            )
            or 0
        )
        closed = (
            session.scalar(
                select(func.count())
                .select_from(JobPosting)
                .where(JobPosting.status == JobStatus.CLOSED)
            )
            or 0
        )
        review = (
            session.scalar(
                select(func.count())
                .select_from(JobPosting)
                .where(JobPosting.needs_location_review.is_(True))
            )
            or 0
        )

        print(f"companies            {companies:>7,}")
        print(f"sources              {sources:>7,}")
        print(f"active jobs          {active:>7,}")
        print(f"  of which Dublin    {dublin:>7,}")
        print(f"closed jobs          {closed:>7,}")
        print(f"needs location review{review:>7,}")

        last = session.execute(
            select(CrawlRun).order_by(CrawlRun.id.desc()).limit(1)
        ).scalar_one_or_none()
        if last:
            print(
                f"\nlast run #{last.id}: {last.sources_ok} ok / "
                f"{last.sources_partial} partial / {last.sources_failed} failed"
            )

        failures = session.execute(
            select(SourceCrawl, Source)
            .join(Source, SourceCrawl.source_id == Source.id)
            .where(SourceCrawl.status != CrawlStatus.OK)
            .order_by(SourceCrawl.id.desc())
            .limit(10)
        ).all()
        if failures:
            print("\nrecent problems:")
            for crawl_row, source in failures:
                print(
                    f"  {source.adapter}:{source.slug} "
                    f"[{crawl_row.status.value}] {crawl_row.error or ''}"
                )
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    with session_scope() as session:
        stmt = (
            select(JobPosting, Company)
            .join(Company, JobPosting.company_id == Company.id)
            .where(JobPosting.status == JobStatus.ACTIVE)
        )
        if not args.all_locations:
            stmt = stmt.where(JobPosting.is_dublin.is_(True))
        if args.query:
            stmt = stmt.where(JobPosting.title.ilike(f"%{args.query}%"))
        stmt = stmt.order_by(JobPosting.first_seen_at.desc()).limit(args.limit)

        rows = session.execute(stmt).all()
        if not rows:
            print("no matching jobs")
            return 0

        for job, company in rows:
            print(f"{company.name:22.22s}  {job.title:52.52s}  {job.location_raw or ''}")
        print(f"\n{len(rows)} shown")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Find a company's ATS from its website, and optionally add it to the registry."""
    from jobfinder.normalize.dedup import normalize_company_name
    from jobfinder.registry.detect import detect_for_website

    result = detect_for_website(args.website)
    if not result.found:
        print(f"no ATS detected for {args.website}: {result.note}")
        return 1

    print(f"{args.website} -> {result.adapter}:{result.slug}  ({result.note})")

    if not args.add:
        print("\nre-run with --add to register it")
        return 0

    init_db()
    name = args.name or args.website
    with session_scope() as session:
        normalized = normalize_company_name(name)
        company = session.execute(
            select(Company).where(Company.normalized_name == normalized)
        ).scalar_one_or_none()
        if company is None:
            company = Company(
                name=name,
                normalized_name=normalized,
                website=args.website,
                careers_url=result.careers_url,
                seed_source="ats-detection",
                coverage_state=CoverageState.ATS_DETECTED,
            )
            session.add(company)
            session.flush()

        exists = session.execute(
            select(Source).where(
                Source.adapter == result.adapter, Source.slug == result.slug
            )
        ).scalar_one_or_none()
        if exists:
            print("source already registered")
            return 0

        session.add(
            Source(company_id=company.id, adapter=result.adapter, slug=result.slug)
        )
    print(f"registered {name}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """Add newly-declared columns and recompute derived fields from stored text."""
    from jobfinder.pipeline.backfill import add_missing_columns, recompute_derived

    added = add_missing_columns()
    if added:
        print(f"added columns: {', '.join(added)}")

    init_db()
    with session_scope() as session:
        count = recompute_derived(session)
    print(f"recomputed derived fields for {count:,} jobs")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    init_db()
    uvicorn.run(
        "jobfinder.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder", description="Dublin job aggregator")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create database tables").set_defaults(func=cmd_init_db)
    sub.add_parser("seed", help="load the company registry").set_defaults(func=cmd_seed)

    p_crawl = sub.add_parser("crawl", help="fetch and reconcile all sources")
    p_crawl.add_argument("--limit", type=int, help="only crawl the first N sources")
    p_crawl.add_argument("--adapter", help="restrict to one adapter")
    p_crawl.set_defaults(func=cmd_crawl)

    sub.add_parser("stats", help="summarize the database").set_defaults(func=cmd_stats)

    p_jobs = sub.add_parser("jobs", help="list active jobs")
    p_jobs.add_argument("query", nargs="?", help="filter by title substring")
    p_jobs.add_argument("--limit", type=int, default=30)
    p_jobs.add_argument("--all-locations", action="store_true", help="do not filter to Dublin")
    p_jobs.set_defaults(func=cmd_jobs)

    p_detect = sub.add_parser("detect", help="find a company's ATS from its website")
    p_detect.add_argument("website", help="e.g. intercom.com")
    p_detect.add_argument("--name", help="company name for the registry")
    p_detect.add_argument("--add", action="store_true", help="register what is found")
    p_detect.set_defaults(func=cmd_detect)

    sub.add_parser(
        "backfill", help="add new columns and recompute derived fields"
    ).set_defaults(func=cmd_backfill)

    p_serve = sub.add_parser("serve", help="run the web portal")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
