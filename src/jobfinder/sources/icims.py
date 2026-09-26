"""iCIMS career-site adapter.

    https://{host}/api/jobs?page={n}

iCIMS portals on `*.icims.com` are built to be framed by the employer's own careers site,
and a direct request is bounced there with a `window.top.location` script rather than
served. The branded site (`careers.pmgroup-global.com`, `www.github.careers`) is where
the jobs are, and it serves them as paginated JSON.

The slug is that branded careers host. A bare `*.icims.com` subdomain — which is all
page fingerprinting ever sees — is also accepted: the portal's bounce names the branded
host, so it is resolved on first fetch.

Pagination must reach the end. `totalCount` is the board's own statement of size, and
stopping well short of it would hand the reconciler a partial list as if it were the
whole board, closing every role on the pages not read.

Every read is narrowed with `country=Ireland` (or the country named after a `|` in the
slug; `*` reads the whole board). The API pages ten at a time, so a global board such
as Aon's or AXA's, with well over a thousand roles, ran past the page ceiling and
failed; filtered, AXA is 17 roles and two pages. `country` matches a role listed in
several countries too, and `full_location` then names every office.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

# Board sizes seen in the registry top out in the low thousands; this bounds a runaway
# loop against a misbehaving API rather than any real board.
MAX_PAGES = 100

# Language variants can make `totalCount` a little larger than the distinct postings, so
# only a shortfall beyond this is treated as an incomplete read.
COMPLETENESS = 0.9

BOUNCE = re.compile(r"window\.top\.location\.href\s*=\s*'([^']+)'")
DEFAULT_COUNTRY = "Ireland"


def split_slug(slug: str) -> tuple[str, str | None]:
    """``(host or portal, country)``; the country is None for a whole-board read."""
    target, _, country = slug.partition("|")
    country = country or DEFAULT_COUNTRY
    return target, None if country == "*" else country


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def resolve_host(slug: str, client: httpx.Client) -> str:
    """The branded careers host for a slug that may be a bare iCIMS subdomain."""
    if "." in slug and not slug.endswith(".icims.com"):
        return slug

    portal = slug if slug.endswith(".icims.com") else f"{slug}.icims.com"
    response = client.get(f"https://{portal}/jobs/search?ss=1")
    response.raise_for_status()
    match = BOUNCE.search(response.text)
    if not match:
        raise ValueError(f"iCIMS portal {portal} did not name its careers site")
    target = match.group(1).replace("\\/", "/")
    host = re.sub(r"^https?://", "", target).split("/", 1)[0]
    if not host:
        raise ValueError(f"iCIMS portal {portal} named an unusable careers site {target!r}")
    return host


def _description(data: dict) -> str | None:
    parts = [
        data.get(key)
        for key in ("description", "responsibilities", "qualifications")
        if isinstance(data.get(key), str) and data.get(key).strip()
    ]
    return "\n\n".join(parts) or None


class ICIMSAdapter(BaseAdapter):
    name = "icims"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        target, country = split_slug(slug)
        host = resolve_host(target, client)
        where = {"country": country} if country else {}

        jobs: dict[str, RawJob] = {}
        total: int | None = None

        for page in range(1, MAX_PAGES + 1):
            response = client.get(f"https://{host}/api/jobs", params={**where, "page": page})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise ValueError(f"unexpected iCIMS payload from {host}")

            if total is None:
                try:
                    total = int(payload.get("totalCount"))
                except (TypeError, ValueError):
                    total = None

            batch = payload["jobs"]
            if not batch:
                break

            for entry in batch:
                data = entry.get("data", entry) if isinstance(entry, dict) else {}
                job_id = str(data.get("req_id") or data.get("slug") or "").strip()
                title = (data.get("title") or "").strip()
                if not job_id or not title:
                    continue
                jobs.setdefault(
                    job_id,
                    RawJob(
                        source_job_id=job_id,
                        title=title,
                        url=f"https://{host}/jobs/{data.get('slug') or job_id}",
                        location_raw=data.get("full_location") or data.get("location_name"),
                        description=_description(data),
                        posted_at=_parse_date(data.get("posted_date")),
                        department=data.get("department") or None,
                    ),
                )

            if total is not None and len(jobs) >= total:
                break
            self.polite_pause()

        if total and len(jobs) < total * COMPLETENESS:
            raise ValueError(
                f"iCIMS read {len(jobs)} of {total} jobs from {host}; "
                "refusing to report an incomplete board"
            )
        return list(jobs.values())


register(ICIMSAdapter())
