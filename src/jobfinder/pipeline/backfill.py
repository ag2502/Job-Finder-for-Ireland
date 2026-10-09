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
from jobfinder.core.models import Company, CoverageState, JobPosting, Source
from jobfinder.normalize.experience import analyze as analyze_experience
from jobfinder.normalize.dedup import fold_accents
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


# Which state to keep when two records of one employer are merged: the one nearer to
# being crawled.
_STATE_RANK = {
    CoverageState.BESPOKE_ADAPTER: 0, CoverageState.ATS_DETECTED: 1,
    CoverageState.GENERIC_EXTRACTION: 2, CoverageState.BLOCKED: 3,
    CoverageState.NO_CAREERS_PAGE: 4, CoverageState.UNRESOLVED: 5,
}


def merge_accent_duplicates(session: Session) -> int:
    """Join companies whose names differ only by accents, and fold the stored keys.

    Company names were normalised without folding accents, so 'Uisce Éireann' and
    'Uisce Eireann' were two employers with their jobs split between them. Only rows
    whose stored key carries an accent are touched, so nothing else is re-keyed. The
    record already holding the folded key is kept where there is one, otherwise the
    oldest; the others' sources and postings move to it and they are deleted. Returns
    the number of companies merged away.
    """
    companies = session.execute(select(Company)).scalars().all()
    groups: dict[str, list[Company]] = {}
    for company in companies:
        groups.setdefault(fold_accents(company.normalized_name), []).append(company)

    merged = 0
    for folded, members in groups.items():
        if len(members) == 1 and members[0].normalized_name == folded:
            continue
        keeper = next((c for c in members if c.normalized_name == folded), None) or min(
            members, key=lambda c: c.id
        )
        for other in members:
            if other is keeper:
                continue
            session.execute(
                Source.__table__.update().where(Source.company_id == other.id).values(company_id=keeper.id)
            )
            session.execute(
                JobPosting.__table__.update()
                .where(JobPosting.company_id == other.id)
                .values(company_id=keeper.id)
            )
            keeper.website = keeper.website or other.website
            keeper.careers_url = keeper.careers_url or other.careers_url
            keeper.coverage_priority = min(keeper.coverage_priority, other.coverage_priority)
            keeper.is_public_listed = keeper.is_public_listed or other.is_public_listed
            if _STATE_RANK.get(other.coverage_state, 9) < _STATE_RANK.get(keeper.coverage_state, 9):
                keeper.coverage_state = other.coverage_state
            session.delete(other)
            merged += 1
        # The losers must be gone before the keeper takes the folded key, which is unique.
        names = [c.name for c in members]
        session.flush()
        keeper.normalized_name = folded
        # Shown with its accents where any record had them: Tirlán, not Tirlan.
        keeper.name = next((n for n in names if fold_accents(n) != n), keeper.name)
    session.flush()
    if merged:
        logger.info("merged %d companies that differed only by accents", merged)
    return merged


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
