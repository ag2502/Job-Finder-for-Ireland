"""Oracle Recruiting Cloud (Oracle HCM Candidate Experience) adapter.

    https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions

Large employers — Dell, JPMorgan, BNY — run their careers sites on Oracle's Candidate
Experience, a single-page app that loads requisitions from this unauthenticated REST
endpoint. Nothing useful is in the page markup; the endpoint is the board.

A site is addressed by host *and* site number, so the slug is ``host|siteNumber`` — for
Dell, ``enterpriseplatform.dell.com|careers``, read straight off the careers URL
``.../hcmUI/CandidateExperience/en/sites/careers``. One Oracle host can carry several
sites (internal, external, campus), and the host alone cannot say which is public.

`TotalJobsCount` is the site's own size, and pagination is checked against it: an
incomplete read would otherwise reach the reconciler as a complete board and close the
roles on unread pages.

The finder's `location` filter narrows a site to one country, and every read uses it
(Ireland unless the slug names another place as a third part, `host|site|place`, or
`*` for the whole board). Reading global boards whole was not only slow but broken:
JPMorgan's has 7,495 roles, past the 6,000 the page ceiling allows, so the fetch failed
every time and its 73 Dublin roles never appeared. Filtered, it is one page.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

PAGE_SIZE = 100
MAX_PAGES = 60
COMPLETENESS = 0.95
DEFAULT_LOCATION = "Ireland"

SITE_URL = re.compile(
    r"https?://([a-z0-9.-]+)/hcmUI/CandidateExperience/[a-z]{2}(?:-[a-z]{2})?/sites/([A-Za-z0-9_-]+)",
    re.I,
)


def slug_from_url(url: str) -> str | None:
    """``host|siteNumber`` from any Candidate Experience URL, or None."""
    match = SITE_URL.search(url)
    return f"{match.group(1).lower()}|{match.group(2)}" if match else None


def split_slug(slug: str) -> tuple[str, str, str | None]:
    """``(host, siteNumber, location)``; the location is None for a whole-board read."""
    host, _, rest = slug.partition("|")
    site, _, location = rest.partition("|")
    if not host or not site:
        raise ValueError(f"Oracle Recruiting slug must be 'host|siteNumber', got {slug!r}")
    location = location or DEFAULT_LOCATION
    return host, site, None if location == "*" else location


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _secondary(requisition: dict, primary: str | None) -> list[str]:
    extras: list[str] = []
    for entry in requisition.get("secondaryLocations") or []:
        name = entry.get("Name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip() and name.strip() not in (primary, *extras):
            extras.append(name.strip())
    return extras


class OracleRecruitingAdapter(BaseAdapter):
    name = "oracle_recruiting"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, site, location = split_slug(slug)
        where = f",location={location}" if location else ""
        endpoint = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

        jobs: dict[str, RawJob] = {}
        total: int | None = None

        for page in range(MAX_PAGES):
            offset = page * PAGE_SIZE
            finder = (
                f"findReqs;siteNumber={site},limit={PAGE_SIZE},offset={offset}{where},"
                "sortBy=POSTING_DATES_DESC"
            )
            # Built by hand: the finder's `;`, `,` and `=` are its own syntax, and letting
            # httpx percent-encode them is not something every Oracle pod tolerates.
            response = client.get(
                f"{endpoint}?onlyData=true&expand=requisitionList.secondaryLocations"
                f"&finder={finder}"
            )
            response.raise_for_status()
            payload = response.json()

            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError(f"unexpected Oracle Recruiting payload from {host}|{site}")
            search = items[0]
            if total is None and isinstance(search.get("TotalJobsCount"), int):
                total = search["TotalJobsCount"]

            batch = search.get("requisitionList") or []
            if not batch:
                break

            for requisition in batch:
                job_id = str(requisition.get("Id") or "").strip()
                title = (requisition.get("Title") or "").strip()
                if not job_id or not title:
                    continue
                primary = requisition.get("PrimaryLocation") or None
                jobs.setdefault(
                    job_id,
                    RawJob(
                        source_job_id=job_id,
                        title=title,
                        url=(
                            f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}"
                            f"/job/{job_id}"
                        ),
                        location_raw=primary,
                        extra_locations=_secondary(requisition, primary),
                        description=requisition.get("ShortDescriptionStr") or None,
                        posted_at=_parse_date(requisition.get("PostedDate")),
                    ),
                )

            if total is not None and len(jobs) >= total:
                break
            self.polite_pause()

        if total and len(jobs) < total * COMPLETENESS:
            raise ValueError(
                f"Oracle Recruiting read {len(jobs)} of {total} jobs from {host}|{site}; "
                "refusing to report an incomplete board"
            )
        return list(jobs.values())


register(OracleRecruitingAdapter())
