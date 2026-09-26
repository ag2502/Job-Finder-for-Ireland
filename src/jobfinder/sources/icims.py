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

import html
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
        if is_classic(target):
            return self._fetch_classic(portal_of(target), client)
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


    def _fetch_classic(self, portal: str, client: httpx.Client) -> list[RawJob]:
        """Read a portal that serves its own list instead of bouncing to a branded site.

        Sisk and Cook Medical run these: /jobs/search?in_iframe=1&pr=N, twenty rows a
        page, the paginator naming the last page. Each row carries the id, title,
        location ("IE-Dublin"), category and the opening lines of the advert. These
        portals serve one employer's roles in every country, so all are kept and the
        pipeline decides which are Dublin.
        """
        jobs: dict[str, RawJob] = {}
        last: int | None = None
        for page in range(MAX_PAGES):
            response = client.get(
                f"https://{portal}/jobs/search", params={"ss": 1, "in_iframe": 1, "pr": page}
            )
            response.raise_for_status()
            rows = parse_classic_page(response.text)
            if last is None:
                pages = [int(n) for n in CLASSIC_PAGE.findall(response.text)]
                last = max(pages) if pages else 0
            for job in rows:
                if job.source_job_id in jobs:
                    continue
                if not job.location_raw:
                    # Some portals leave the location off the list; the role's page has it.
                    job.location_raw = self._classic_location(job.url, client)
                jobs[job.source_job_id] = job
            if not rows or page >= last:
                break
            self.polite_pause()
        if not jobs:
            raise ValueError(f"no vacancies listed on iCIMS portal {portal}")
        return list(jobs.values())


    def _classic_location(self, url: str, client: httpx.Client) -> str | None:
        try:
            response = client.get(url, params={"in_iframe": 1})
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        finally:
            self.polite_pause()
        match = CLASSIC_LOCATIONS.search(response.text)
        return _classic_place(html.unescape(match.group(1)).strip()) if match else None


CLASSIC_LOCATIONS = re.compile(r"Job Locations\s*</[^>]+>\s*(?:<[^>]+>\s*)*([^<]+)")
CLASSIC_ANCHOR = re.compile(
    r'<a href="(https://[^"]+/jobs/(\d+)/[^"]+)"[^>]*class="iCIMS_Anchor"[^>]*>.*?<h3[^>]*>(.*?)</h3>', re.S
)
CLASSIC_PAGE = re.compile(r'href="[^"]*/jobs/search\?pr=(\d+)')


def portal_of(target: str) -> str:
    target = target.removeprefix("classic:")
    return target if target.endswith(".icims.com") else f"{target}.icims.com"


def is_classic(target: str) -> bool:
    """A bare portal name whose slug says it serves its own list: `classic:portal`."""
    return target.startswith("classic:")


def _classic_field(row: str, label: str) -> str | None:
    match = re.search(rf'field-label">{label}</span>\s*<span[^>]*>(.*?)</span>', row, re.S)
    if not match:
        match = re.search(rf"{label}\s*<span[^>]*>(.*?)</span>", row, re.S)
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", match.group(1))).strip() if match else None


def _classic_place(value: str | None) -> str | None:
    """"IE-Dublin" -> "Dublin, IE"; the country code leads on these portals."""
    if not value:
        return None
    places = []
    for part in re.split(r"\s*\|\s*", value):
        code, _, rest = part.partition("-")
        places.append(f"{rest.strip()}, {code.strip()}" if rest and len(code.strip()) == 2 else part.strip())
    return " | ".join(places)


def parse_classic_page(page: str) -> list[RawJob]:
    jobs = []
    for chunk in page.split('<div class="row">'):
        anchor = CLASSIC_ANCHOR.search(chunk)
        if not anchor:
            continue
        description = re.search(r'<div class="col-xs-12 description">(.*?)</div>', chunk, re.S)
        jobs.append(RawJob(
            source_job_id=anchor.group(2),
            title=html.unescape(re.sub(r"<[^>]+>|\s+", " ", anchor.group(3))).strip(),
            url=anchor.group(1).replace("?in_iframe=1", "").replace("&amp;", "&"),
            location_raw=_classic_place(_classic_field(chunk, "Location")),
            description=html.unescape(re.sub(r"<[^>]+>", " ", description.group(1))).strip() if description else None,
            department=_classic_field(chunk, "Category"),
        ))
    return jobs


register(ICIMSAdapter())
