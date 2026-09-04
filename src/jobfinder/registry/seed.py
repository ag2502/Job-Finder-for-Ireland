"""Loading the company registry from seed files.

Seeding is idempotent: rerunning it updates existing rows rather than duplicating them,
so the CSV can be edited and reloaded freely.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.config import DATA_DIR
from jobfinder.core.models import Company, CoverageState, Source
from jobfinder.normalize.dedup import normalize_company_name

logger = logging.getLogger(__name__)

DEFAULT_SEED = DATA_DIR / "seed_companies.csv"


def seed_companies(session: Session, path: Path | None = None) -> tuple[int, int]:
    """Load companies and sources from a CSV. Returns (companies_added, sources_added)."""
    path = path or DEFAULT_SEED
    if not path.exists():
        raise FileNotFoundError(f"seed file not found: {path}")

    companies_added = 0
    sources_added = 0

    # utf-8-sig strips the BOM some spreadsheet exports prepend, which would otherwise
    # corrupt the first column name.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            adapter = (row.get("adapter") or "").strip()
            slug = (row.get("slug") or "").strip()
            if not name or not adapter or not slug:
                continue

            normalized = normalize_company_name(name)
            company = session.execute(
                select(Company).where(Company.normalized_name == normalized)
            ).scalar_one_or_none()

            listed = (row.get("is_public_listed") or "").strip().lower() in {
                "true", "yes", "1",
            }

            if company is None:
                company = Company(
                    name=name,
                    normalized_name=normalized,
                    seed_source=(row.get("seed_source") or "seed").strip(),
                    coverage_priority=int(row.get("coverage_priority") or 5),
                    is_public_listed=listed,
                    coverage_state=CoverageState.ATS_DETECTED,
                )
                session.add(company)
                session.flush()
                companies_added += 1
            else:
                company.coverage_state = CoverageState.ATS_DETECTED
                company.is_public_listed = listed
                company.coverage_priority = int(row.get("coverage_priority") or 5)

            source = session.execute(
                select(Source).where(Source.adapter == adapter, Source.slug == slug)
            ).scalar_one_or_none()

            if source is None:
                session.add(
                    Source(
                        company_id=company.id,
                        adapter=adapter,
                        slug=slug,
                        tier=1,
                        enabled=True,
                    )
                )
                sources_added += 1

    session.flush()
    logger.info("seeded %d companies, %d sources", companies_added, sources_added)
    return companies_added, sources_added
