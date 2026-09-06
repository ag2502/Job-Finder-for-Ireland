"""Write the two coverage reports as CSV.

    python scripts/build_reports.py

`reports/companies.csv`  - every employer in the registry and how it is reached.
`reports/platforms.csv`  - every job platform relevant to Ireland and whether this
                           project can read it.

The platform report is a *catalogue* joined to live counts, not a dump of what happens
to be in the database. That distinction is the point: a report listing only the nine
platforms currently in use would answer "what do we read?" while quietly failing to
answer the question that actually matters — "what is out there, and what are we
missing?". `data/platform_catalogue.csv` carries the full landscape, including the
platforms that are deliberately excluded and why, and this script fills in the numbers.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import func, select

from jobfinder.core.config import DATA_DIR, PROJECT_ROOT
from jobfinder.core.db import session_scope
from jobfinder.core.models import Company, CoverageState, JobPosting, JobStatus, Source
from jobfinder.sources import load_adapters

REPORTS = PROJECT_ROOT / "reports"
CATALOGUE = DATA_DIR / "platform_catalogue.csv"

CRAWLED_STATES = {
    CoverageState.ATS_DETECTED,
    CoverageState.BESPOKE_ADAPTER,
    CoverageState.GENERIC_EXTRACTION,
}

# Order the platform report by how actionable each row is.
STATUS_ORDER = {
    "in use": 0,
    "built": 1,
    "open api": 2,
    "needs key": 3,
    "detected only": 4,
    "scrape only": 5,
    "not accessible": 6,
}


def write_companies(session) -> int:
    dublin = dict(
        session.execute(
            select(JobPosting.company_id, func.count())
            .where(JobPosting.status == JobStatus.ACTIVE, JobPosting.is_dublin.is_(True))
            .group_by(JobPosting.company_id)
        ).all()
    )
    everywhere = dict(
        session.execute(
            select(JobPosting.company_id, func.count())
            .where(JobPosting.status == JobStatus.ACTIVE)
            .group_by(JobPosting.company_id)
        ).all()
    )

    sources: dict[int, list[Source]] = {}
    for source in session.execute(
        select(Source).where(Source.enabled.is_(True))
    ).scalars():
        sources.setdefault(source.company_id, []).append(source)

    rows = []
    for company in session.execute(select(Company)).scalars():
        mine = sources.get(company.id, [])
        rows.append(
            {
                "company": company.name,
                "platform": ";".join(sorted({s.adapter for s in mine})),
                "platform_slug": ";".join(s.slug for s in mine),
                "how_covered": (
                    "crawled"
                    if mine
                    else "directory link"
                    if company.careers_url
                    else "not yet resolved"
                ),
                "dublin_jobs_active": dublin.get(company.id, 0),
                "all_jobs_active": everywhere.get(company.id, 0),
                "coverage_state": company.coverage_state.value,
                "priority": company.coverage_priority,
                "sector": company.seed_source or "",
                "website": company.website or "",
                "careers_url": company.careers_url or "",
            }
        )

    # Employers with live roles first, then those we can crawl, then alphabetically:
    # the top of the file should be the part a searcher would actually act on.
    rows.sort(
        key=lambda r: (
            -r["dublin_jobs_active"],
            r["how_covered"] != "crawled",
            r["company"].lower(),
        )
    )
    _write(REPORTS / "companies.csv", rows)
    return len(rows)


def write_platforms(session) -> int:
    load_adapters()

    companies = dict(
        session.execute(
            select(Source.adapter, func.count(func.distinct(Source.company_id)))
            .where(Source.enabled.is_(True))
            .group_by(Source.adapter)
        ).all()
    )
    source_counts = dict(
        session.execute(
            select(Source.adapter, func.count())
            .where(Source.enabled.is_(True))
            .group_by(Source.adapter)
        ).all()
    )
    dublin = dict(
        session.execute(
            select(Source.adapter, func.count())
            .select_from(JobPosting)
            .join(Source, Source.id == JobPosting.source_id)
            .where(JobPosting.status == JobStatus.ACTIVE, JobPosting.is_dublin.is_(True))
            .group_by(Source.adapter)
        ).all()
    )

    rows = []
    for entry in _read_catalogue():
        name = entry["platform"]
        # The catalogue records intent; the database records reality. Where a platform
        # has live sources, reality wins — otherwise a platform whose adapter was
        # written months ago would still be reported as "built".
        status = entry["status"]
        if companies.get(name):
            status = "in use"

        rows.append(
            {
                "platform": name,
                "kind": entry["kind"],
                "tier": entry["tier"],
                "access": entry["access"],
                "status": status,
                "companies": companies.get(name, 0),
                "sources": source_counts.get(name, 0),
                "dublin_jobs_active": dublin.get(name, 0),
                "notes": entry["notes"],
            }
        )

    rows.sort(
        key=lambda r: (
            STATUS_ORDER.get(r["status"], 9),
            -r["dublin_jobs_active"],
            r["platform"],
        )
    )
    _write(REPORTS / "platforms.csv", rows)
    return len(rows)


def _read_catalogue() -> list[dict]:
    with CATALOGUE.open(newline="", encoding="utf-8-sig") as fh:
        return [
            row
            for row in csv.DictReader(fh)
            # The catalogue is commented for humans; `#` rows are not platforms.
            if row.get("platform") and not row["platform"].startswith("#")
        ]


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    with session_scope() as session:
        companies = write_companies(session)
        platforms = write_platforms(session)
    print(f"reports/companies.csv  {companies:>4} employers")
    print(f"reports/platforms.csv  {platforms:>4} platforms")


if __name__ == "__main__":
    main()
