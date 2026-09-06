"""Importing the company universe.

`seed_companies` loads companies that already have a known ATS and slug. This module
loads the other kind: a name and a website, with no idea yet where its jobs live. Those
rows enter as `UNRESOLVED` and are resolved later by `registry.bulk_detect`.

That split is the point. Maintaining a hand-verified `adapter,slug` pair per company
caps the registry at whatever a person can curate — a few dozen. Maintaining only
`name,website` scales to whatever list can be obtained, and detection does the rest.
Coverage is registry size multiplied by detection hit rate, and this is the term that
was two orders of magnitude too small.

## Where the list comes from

Any CSV with `name` and `website` columns works, which is deliberate: the Irish company
universe is not published as one file. It has to be assembled from the IDA's client
list, Enterprise Ireland's portfolio, Euronext Dublin, TechIreland, the CRO register and
the public-sector bodies, each of which arrives in a different shape. Rather than
encoding a parser per source, this reads the common denominator and records where each
row came from in `seed_source`, so coverage can be reported per origin.

## Being wrong is cheap, and visible

A mistyped or dead domain does not corrupt anything. Detection fails to reach it, the
company is marked `UNRESOLVED`, and it shows up in the coverage report as work to do.
That is why importing a large imperfect list beats curating a small perfect one: the
errors are self-reporting, and `coverage_state` is where they report to.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.config import DATA_DIR
from jobfinder.core.models import Company, CoverageState
from jobfinder.normalize.dedup import normalize_company_name

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE = DATA_DIR / "ireland_companies.csv"

TRUE_VALUES = {"true", "yes", "1", "y"}


@dataclass
class ImportStats:
    rows: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.rows} rows: {self.added} added, {self.updated} updated, "
            f"{self.skipped} skipped"
        )


def canonical_website(website: str) -> str | None:
    """Reduce a website to `https://host` so the same company is not imported twice.

    Lists disagree about `www.`, trailing slashes, http vs https and tracking query
    strings, and a company that arrives from three sources in three spellings would
    otherwise become three companies with three separate detection budgets.
    """
    website = (website or "").strip()
    if not website:
        return None

    if "//" not in website:
        website = f"https://{website}"

    parts = urlsplit(website)
    host = (parts.netloc or parts.path).strip("/").lower()
    if not host or "." not in host:
        return None
    host = host.removeprefix("www.")

    return f"https://{host}"


def import_universe(
    session: Session,
    path: Path | None = None,
    *,
    default_priority: int = 4,
    default_source: str = "universe",
) -> ImportStats:
    """Load `name,website` rows into the registry as companies awaiting detection.

    Idempotent: a company already present keeps its coverage state and its sources, and
    only gains a website if it had none. Re-running an import must never reset a
    company that detection has already resolved.
    """
    path = path or DEFAULT_UNIVERSE
    if not path.exists():
        raise FileNotFoundError(f"universe file not found: {path}")

    stats = ImportStats()
    seen_names: set[str] = set()

    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            stats.rows += 1

            name = (row.get("name") or "").strip()
            website = canonical_website(row.get("website") or "")
            if not name or not website:
                stats.skipped += 1
                continue

            normalized = normalize_company_name(name)
            if normalized in seen_names:
                # Duplicate within this file; the first spelling wins.
                stats.skipped += 1
                continue
            seen_names.add(normalized)

            company = session.execute(
                select(Company).where(Company.normalized_name == normalized)
            ).scalar_one_or_none()

            priority = _int(row.get("coverage_priority"), default_priority)
            listed = (row.get("is_public_listed") or "").strip().lower() in TRUE_VALUES
            seed_source = (row.get("seed_source") or default_source).strip()

            if company is None:
                session.add(
                    Company(
                        name=name,
                        normalized_name=normalized,
                        website=website,
                        cro_number=(row.get("cro_number") or "").strip() or None,
                        is_public_listed=listed,
                        coverage_priority=priority,
                        seed_source=seed_source,
                        coverage_state=CoverageState.UNRESOLVED,
                    )
                )
                stats.added += 1
                continue

            changed = False
            if not company.website:
                company.website = website
                changed = True
            if not company.cro_number and (row.get("cro_number") or "").strip():
                company.cro_number = row["cro_number"].strip()
                changed = True
            # A list that asserts higher importance wins; nothing is ever demoted, so
            # re-importing a generic list cannot bury a company a curated list promoted.
            if priority < company.coverage_priority:
                company.coverage_priority = priority
                changed = True
            if listed and not company.is_public_listed:
                company.is_public_listed = True
                changed = True

            if changed:
                stats.updated += 1
            else:
                stats.skipped += 1

    session.flush()
    logger.info("universe import: %s", stats)
    return stats


def _int(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip())
    except (TypeError, ValueError):
        return default
