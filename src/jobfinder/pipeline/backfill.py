"""Schema top-ups and derived-field recomputation.

Derived columns (experience level, location flags) are computed at reconciliation time,
so adding a new one leaves every existing row null until it is next crawled. Rather than
force a full re-crawl — which re-hits every upstream API for data already stored — this
adds any missing columns and recomputes from the descriptions already in the database.

It is deliberately narrow: `ADD COLUMN` only, never dropping or rewriting existing data.
Anything more involved belongs in a real migration.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from jobfinder.core.db import engine
from jobfinder.core.models import JobPosting
from jobfinder.normalize.experience import analyze as analyze_experience
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import dublin_in_advert, normalize_location
from jobfinder.normalize.text import html_to_text

logger = logging.getLogger(__name__)

# column name -> DDL type, for columns added after the initial schema.
ADDED_COLUMNS: dict[str, str] = {
    "min_years_required": "INTEGER",
    "years_inferred": "BOOLEAN DEFAULT 0",
    "is_internship": "BOOLEAN DEFAULT 0",
    "is_graduate": "BOOLEAN DEFAULT 0",
    "is_ireland": "BOOLEAN DEFAULT 0",
    "region": "VARCHAR(32)",
    "employment_type": "VARCHAR(64)",
    "is_part_time": "BOOLEAN DEFAULT 0",
}


def add_missing_columns() -> list[str]:
    """Add any newly-declared columns to an existing job_postings table."""
    inspector = inspect(engine)
    if "job_postings" not in inspector.get_table_names():
        return []

    existing = {col["name"] for col in inspector.get_columns("job_postings")}
    added: list[str] = []

    with engine.begin() as connection:
        for name, ddl in ADDED_COLUMNS.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE job_postings ADD COLUMN {name} {ddl}")
                )
                added.append(name)
                logger.info("added column job_postings.%s", name)
        # Indexes on the added columns, which `create_all` skips on an existing table.
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_job_active_ireland ON job_postings (status, is_ireland)"
        ))

    return added


def recompute_regions(session: Session, *, batch: int = 500) -> int:
    """Fill `is_ireland` and `region` for every stored posting.

    Run once when the columns are added, so roles are not left out of an Ireland-wide
    search until each source's next successful crawl. A second office is not stored, so
    a posting already known to be Dublin through one keeps that verdict.
    """
    jobs = session.execute(select(JobPosting)).scalars().all()
    for index, job in enumerate(jobs, 1):
        location = normalize_location(job.location_raw)
        if job.is_dublin:
            job.is_ireland, job.region = True, "Dublin"
        else:
            job.is_ireland, job.region = location.is_ireland, location.region
        if index % batch == 0:
            session.flush()
    session.flush()
    return len(jobs)


def recompute_part_time(session: Session, *, batch: int = 500) -> int:
    """Fill `is_part_time` for every stored posting, from its title and advert.

    Run once when the column is added, so part-time roles already in the database are
    found by the "Part-time only" switch before each source's next crawl.
    """
    jobs = session.execute(select(JobPosting)).scalars().all()
    for index, job in enumerate(jobs, 1):
        job.is_part_time = is_part_time(job.title, job.description, job.employment_type)
        if index % batch == 0:
            session.flush()
    session.flush()
    return len(jobs)


def recompute_derived(session: Session, *, batch: int = 500) -> int:
    """Recompute experience and location flags from stored text."""
    jobs = session.execute(select(JobPosting)).scalars().all()

    for index, job in enumerate(jobs, 1):
        job.description = html_to_text(job.description)
        experience = analyze_experience(job.title, job.description)
        job.min_years_required = experience.min_years
        job.years_inferred = experience.inferred
        job.is_internship = experience.is_internship
        job.is_graduate = experience.is_graduate
        job.is_part_time = is_part_time(job.title, job.description, job.employment_type)

        location = normalize_location(job.location_raw)
        if not (job.location_raw or "").strip():
            location.is_dublin = dublin_in_advert(f"{job.title}\n{job.description or ''}")
        job.is_dublin = location.is_dublin
        job.is_ireland = location.is_ireland or location.is_dublin
        job.region = "Dublin" if location.is_dublin else location.region
        job.is_remote = location.is_remote or job.is_remote
        job.needs_location_review = location.needs_review

        if index % batch == 0:
            session.flush()

    session.flush()
    return len(jobs)
