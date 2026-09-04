"""Workday adapter.

Workday is the dominant ATS among Dublin's multinationals, which makes this the single
highest-value adapter in the project — one Workday tenant returned 119 Dublin postings
where a whole category of smaller sources returns a handful.

Unlike the other Tier 1 boards there is no single public endpoint. Each customer runs
its own tenant, so a source is addressed by a compound slug:

    tenant:wdhost:site      e.g.  accenture:wd103:AccentureCareers

    POST https://{tenant}.{wdhost}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
         {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Dublin"}

Two shape details drive the implementation:

* The list endpoint omits descriptions and gives only a *relative* date ("Posted
  Yesterday"). The per-job detail endpoint carries the full description and a real
  `startDate`, but costs one request each.
* So the adapter filters to Dublin from the list response first and only then fetches
  details for the survivors. On a tenant with thousands of global roles that is the
  difference between ~120 detail requests and several thousand.

`searchText` is a free-text search rather than a location facet — location facet IDs
differ per tenant and would need discovery. Searching several terms and merging by
`externalPath` recovers the recall that a single term would lose.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from jobfinder.core.config import settings
from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
MAX_PAGES = 50  # safety stop; 1000 postings per search term is ample for one city
SEARCH_TERMS = ("Dublin", "Ireland")

_RELATIVE_DAYS = re.compile(r"(\d+)\+?\s*days?\s*ago", re.IGNORECASE)


def parse_posted_on(value: str | None) -> datetime | None:
    """Turn Workday's relative wording into a timestamp.

    Values look like "Posted Today", "Posted Yesterday", "Posted 14 Days Ago" or
    "Posted 30+ Days Ago". The "30+" form is a floor rather than an exact age, which is
    why detail responses are preferred when available.
    """
    if not value:
        return None
    text = value.strip().lower()
    now = datetime.now(timezone.utc)

    if "today" in text:
        return now
    if "yesterday" in text:
        return now - timedelta(days=1)

    match = _RELATIVE_DAYS.search(text)
    if match:
        return now - timedelta(days=int(match.group(1)))
    return None


def parse_slug(slug: str) -> tuple[str, str, str]:
    """Split "tenant:wdhost:site" into its parts."""
    parts = slug.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"workday slug must be 'tenant:wdhost:site', got {slug!r}"
        )
    return parts[0], parts[1], parts[2]


class WorkdayAdapter(BaseAdapter):
    name = "workday"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, wdhost, site = parse_slug(slug)
        base = f"https://{tenant}.{wdhost}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

        listings = self._collect_listings(base, client)
        logger.info("workday %s: %d unique listings across search terms", slug, len(listings))

        # Filter before enriching - see module docstring.
        dublin = [item for item in listings.values() if self._looks_dublin(item)]
        logger.info("workday %s: %d look like Dublin, fetching details", slug, len(dublin))

        jobs: list[RawJob] = []
        for item in dublin:
            jobs.append(self._build_job(base, tenant, wdhost, site, item, client))
        return jobs

    def _collect_listings(
        self, base: str, client: httpx.Client
    ) -> dict[str, dict]:
        """Page through every search term, merging on externalPath."""
        found: dict[str, dict] = {}

        for term in SEARCH_TERMS:
            offset = 0
            for _ in range(MAX_PAGES):
                response = client.post(
                    f"{base}/jobs",
                    json={
                        "appliedFacets": {},
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "searchText": term,
                    },
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                postings = payload.get("jobPostings") or []
                if not postings:
                    break

                for item in postings:
                    path = item.get("externalPath")
                    if path:
                        found.setdefault(path, item)

                offset += PAGE_SIZE
                if offset >= (payload.get("total") or 0):
                    break
                self.polite_pause()

        return found

    @staticmethod
    def _list_location(item: dict) -> str | None:
        """Recover a location string from the list response.

        `bulletFields` is typically [requisitionId, location], and the externalPath is
        shaped /job/{Location}/{Title}_{ReqId}. Either can be missing, so both are tried.
        """
        bullets = [b for b in (item.get("bulletFields") or []) if b]
        if len(bullets) >= 2:
            return bullets[-1]

        path = item.get("externalPath") or ""
        match = re.match(r"^/job/([^/]+)/", path)
        if match:
            return match.group(1).replace("-", " ")
        return None

    def _looks_dublin(self, item: dict) -> bool:
        location = self._list_location(item)
        if location and normalize_location(location).is_dublin:
            return True
        # The title itself sometimes carries the office when the location is generic.
        title = item.get("title") or ""
        return normalize_location(title).is_dublin

    def _build_job(
        self,
        base: str,
        tenant: str,
        wdhost: str,
        site: str,
        item: dict,
        client: httpx.Client,
    ) -> RawJob:
        path = item["externalPath"]
        req_id = next(iter(item.get("bulletFields") or []), None) or path
        public_url = f"https://{tenant}.{wdhost}.myworkdayjobs.com/{site}{path}"

        location = self._list_location(item)
        description = None
        posted_at = parse_posted_on(item.get("postedOn"))

        # Enrichment is best-effort: a detail fetch that fails must not discard the
        # listing we already have, which is why this is caught rather than propagated.
        try:
            detail = client.get(f"{base}{path}")
            detail.raise_for_status()
            info = detail.json().get("jobPostingInfo") or {}
            description = info.get("jobDescription")
            location = info.get("location") or location
            public_url = info.get("externalUrl") or public_url
            if info.get("startDate"):
                try:
                    posted_at = datetime.fromisoformat(info["startDate"]).replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    pass
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("workday detail fetch failed for %s: %s", path, exc)

        self.polite_pause()

        return RawJob(
            source_job_id=str(req_id),
            title=item.get("title") or "",
            url=public_url,
            location_raw=location,
            description=description,
            posted_at=posted_at,
        )


register(WorkdayAdapter())
