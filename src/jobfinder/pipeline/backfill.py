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
from jobfinder.normalize.location import normalize_location
from jobfinder.normalize.text import html_to_text

logger = logging.getLogger(__name__)

# column name -> DDL type, for columns added after the initial schema.
ADDED_COLUMNS: dict[str, str] = {
    "min_years_required": "INTEGER",
    "years_inferred": "BOOLEAN DEFAULT 0",
    "is_internship": "BOOLEAN DEFAULT 0",
    "is_graduate": "BOOLEAN DEFAULT 0",
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

    return added


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

        location = normalize_location(job.location_raw)
        job.is_dublin = location.is_dublin
        job.is_remote = location.is_remote or job.is_remote
        job.needs_location_review = location.needs_review

        if index % batch == 0:
            session.flush()

    session.flush()
    return len(jobs)
