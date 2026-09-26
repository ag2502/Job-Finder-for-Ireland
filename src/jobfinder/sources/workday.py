"""Workday adapter.

Workday is the dominant ATS among Dublin's multinationals, which makes this the single
highest-value adapter in the project — one Workday tenant returned 119 Dublin postings
where a whole category of smaller sources returns a handful.

Unlike the other Tier 1 boards there is no single public endpoint. Each customer runs
its own tenant, so a source is addressed by a compound slug:

    tenant:wdhost:site      e.g.  accenture:wd103:AccentureCareers

    POST https://{tenant}.{wdhost}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
         {"appliedFacets": {...}, "limit": 20, "offset": 0, "searchText": ""}

The adapter returns a tenant's **Irish** postings and leaves Dublin to the pipeline,
which checks every office a role lists. Four details of the API drive how:

* `total` is reported on the first page only. Every later page says `"total": 0`, so a
  loop that re-reads it stops after twenty postings. That is how Mastercard, with 60
  Dublin roles, was crawled as 37: the first page of "Dublin" and of "Ireland", merged.
* Location is best read through the tenant's own facets, not free text. Country
  facets use Workday's global reference id for Ireland on every tenant, under
  whatever name the tenant gave the facet (`locationCountry`, `Location_Country`,
  `Country_and_Jurisdiction`, ...). Tenants without one still have a `locations`
  facet whose values name their offices, and that facet counts a role under each of
  its offices, not only the first.
* The list response names only the first office, or just "6 Locations". The per-job
  detail carries the full description, a real `startDate`, and `additionalLocations`,
  which is where a Stockholm-led role that is also open in Dublin says so.
* `searchText` is kept as a safety net for tenants whose facets name no Irish office,
  and its results are filtered on what the list says before any detail is fetched,
  since "Dublin" also matches every London role that mentions the Dublin team.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
MAX_PAGES = 50  # safety stop: 1000 postings per query is far above any tenant's Irish set
SEARCH_TERMS = ("Dublin", "Ireland")

# Workday's reference id for the country Ireland. It is the same on every tenant,
# because it comes from Workday's global country table rather than the tenant's setup.
IRELAND_COUNTRY_ID = "04a05835925f45b3a59406a2a6b72c8a"

_IRISH_WORDS = re.compile(r"\b(ireland|irl|eire|éire)\b", re.IGNORECASE)
_MULTI_LOCATION = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)
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


def is_irish_place(text: str | None) -> bool:
    """True for a location string naming Ireland or a Dublin office."""
    if not text:
        return False
    return bool(_IRISH_WORDS.search(text)) or normalize_location(text).is_dublin


def irish_facets(facets: list[dict] | None) -> list[dict[str, list[str]]]:
    """The facet filters that select a tenant's Irish postings.

    Returns one `appliedFacets` body per filter: the country facet if the tenant has
    one, and its Irish office values if it has those. Both are kept because they are
    not always the same set — a tenant's country facet may count a role only under its
    first office, while its `locations` facet counts every office.
    """
    country: dict[str, list[str]] = {}
    offices: dict[str, list[str]] = {}

    def walk(entries: list[dict], parameter: str | None) -> None:
        for entry in entries or []:
            name = entry.get("facetParameter") or parameter
            values = entry.get("values")
            if values is not None:
                walk(values, name)
                continue
            value_id = entry.get("id")
            if not (name and value_id):
                continue
            if value_id == IRELAND_COUNTRY_ID:
                country.setdefault(name, []).append(value_id)
            elif "location" in name.lower() and is_irish_place(entry.get("descriptor")):
                offices.setdefault(name, []).append(value_id)

    walk(facets or [], None)

    filters: list[dict[str, list[str]]] = []
    # One filter per facet: values within a facet are OR-ed by Workday, but separate
    # facets are AND-ed, which would ask for roles that are in every listed place.
    for name, ids in country.items():
        filters.append({name: ids})
    for name, ids in offices.items():
        filters.append({name: ids})
    return filters


class WorkdayAdapter(BaseAdapter):
    name = "workday"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, wdhost, site = parse_slug(slug)
        base = f"https://{tenant}.{wdhost}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

        first = self._post(base, client, {}, "", 0)
        filters = irish_facets(first.get("facets"))

        # Postings a facet placed in Ireland need no further evidence. Postings found
        # only by free text do, and are held to what their list entry says.
        by_facet: dict[str, dict] = {}
        by_text: dict[str, dict] = {}
        complete = True

        for applied in filters:
            listings, whole = self._collect(base, client, applied, "")
            by_facet.update(listings)
            complete = complete and whole

        for term in SEARCH_TERMS:
            listings, whole = self._collect(base, client, {}, term)
            complete = complete and whole
            for path, item in listings.items():
                if path not in by_facet and self._worth_a_detail(item):
                    by_text.setdefault(path, item)

        logger.info(
            "workday %s: %d Irish by facet (%d filters), %d more by search",
            slug, len(by_facet), len(filters), len(by_text),
        )

        jobs: list[RawJob] = []
        for path, item in by_facet.items():
            jobs.append(self._build_job(base, tenant, wdhost, site, item, client))
        for path, item in by_text.items():
            job = self._build_job(base, tenant, wdhost, site, item, client)
            # A multi-office role is fetched on suspicion; keep it only if one of its
            # offices turned out to be Irish.
            if any(is_irish_place(loc) for loc in [job.location_raw, *job.extra_locations]):
                jobs.append(job)

        return jobs if complete else PartialJobs(jobs)

    def _post(
        self,
        base: str,
        client: httpx.Client,
        applied: dict[str, list[str]],
        search_text: str,
        offset: int,
    ) -> dict:
        response = client.post(
            f"{base}/jobs",
            json={
                "appliedFacets": applied,
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": search_text,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def _collect(
        self,
        base: str,
        client: httpx.Client,
        applied: dict[str, list[str]],
        search_text: str,
    ) -> tuple[dict[str, dict], bool]:
        """Page through one query, merging on externalPath.

        Returns the listings and whether the query was read to the end. `total` is
        taken from the first page alone, because Workday reports it there and nowhere
        else; a short or empty page also ends the query, which covers a tenant that
        omits it altogether.
        """
        found: dict[str, dict] = {}
        total: int | None = None
        offset = 0

        for _ in range(MAX_PAGES):
            payload = self._post(base, client, applied, search_text, offset)
            if total is None:
                total = payload.get("total") or 0
            postings = payload.get("jobPostings") or []

            for item in postings:
                path = item.get("externalPath")
                if path:
                    found.setdefault(path, item)

            offset += PAGE_SIZE
            if len(postings) < PAGE_SIZE or offset >= total:
                return found, True
            self.polite_pause()

        logger.warning(
            "workday %s %r: stopped at %d pages of %d postings", base, search_text or applied,
            MAX_PAGES, total,
        )
        return found, False

    @staticmethod
    def _list_location(item: dict) -> str | None:
        """Recover a location string from the list response.

        `locationsText` is the field the careers page shows. Older tenants put the
        location in `bulletFields` ([requisitionId, location]) instead, and the
        externalPath is shaped /job/{Location}/{Title}_{ReqId}, so both are tried.
        """
        text = item.get("locationsText")
        if text:
            return text

        bullets = [b for b in (item.get("bulletFields") or []) if b]
        if len(bullets) >= 2:
            return bullets[-1]

        path = item.get("externalPath") or ""
        match = re.match(r"^/job/([^/]+)/", path)
        if match:
            return match.group(1).replace("-", " ")
        return None

    def _worth_a_detail(self, item: dict) -> bool:
        """Whether a free-text hit might be Irish, judged from its list entry alone."""
        location = self._list_location(item)
        if location and _MULTI_LOCATION.match(location):
            return True  # the offices are only in the detail
        if is_irish_place(location):
            return True
        # The title itself sometimes carries the office when the location is generic.
        return normalize_location(item.get("title") or "").is_dublin

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
        extra: list[str] = []
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
            extra = [loc for loc in info.get("additionalLocations") or [] if loc]
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
            extra_locations=extra,
        )


register(WorkdayAdapter())
