"""gradireland — Ireland's graduate recruitment board.

    POST https://gradireland.com/ext/svc/inferno-search-service-1-0/search

Graduate schemes, graduate jobs and internships from employers across Ireland, many of
which advertise entry-level roles nowhere else. The site is a Gatsby front end over a
search service, and its pages carry no `JobPosting` markup, so the adapter asks the
service directly with the same query the site's own search page sends: every live
opportunity (application deadline not yet passed), fifty at a time, in a stable order.

The service answers an outside request only when it carries the headers the site's
front end sends (`x-host`, `origin`, `referer`); without them it returns an empty
result rather than an error, which is why an empty first page is treated as a failure.

`robots.txt` allows everything. Each opportunity names its employer, so it is filed
under that company, as with every multi-employer board.
"""

from __future__ import annotations

import html
import re

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

ORIGIN = "https://gradireland.com"
SEARCH = ORIGIN + "/ext/svc/inferno-search-service-1-0/search"
HEADERS = {
    "x-host": "users.gradireland.com",
    "origin": ORIGIN,
    "referer": ORIGIN + "/graduate-jobs",
}
PAGE_SIZE = 50
MAX_PAGES = 40

FIELDS = [
    "nid", "uuid", "title", "type", "body", "url", "location", "regions",
    "parent_organisation_title", "source_organisation_name", "application_url",
    "application_deadline_date", "opportunity_start_date", "opportunity_type",
]


def query(offset: int) -> dict:
    return {
        "fields": FIELDS,
        "keys": [""],
        "groupBy": None,
        "conditionGroup": {
            "conjunction": "AND",
            "groups": [
                {
                    "conjunction": "OR",
                    "conditions": [
                        {"name": "application_deadline_date", "value": ["0", "NOW"], "operator": "NOT BETWEEN"}
                    ],
                    "tags": ["facet:application_deadline_date"],
                },
                {
                    "conjunction": "OR",
                    "conditions": [{"name": "type", "value": "opportunity", "operator": "="}],
                    "tags": ["facet:type"],
                },
            ],
        },
        "facets": None,
        # The site sorts randomly; paging needs an order that holds between requests.
        "sort": [{"field": "nid", "value": "asc"}],
        "conditions": [],
        "limit": PAGE_SIZE,
        "offset": offset,
        "includePromoted": False,
    }


def _location(doc: dict) -> str | None:
    location = doc.get("location")
    if isinstance(location, list):
        location = ", ".join(str(part) for part in location if part)
    if location:
        location = html.unescape(str(location)).strip()
    regions = [r for r in doc.get("regions") or [] if r and r != "Europe"]
    # Regions run most specific first ("County Dublin", "Ireland"); they are the
    # structured fallback when the free-text location is absent.
    region_text = ", ".join(regions) or None
    if location and region_text and "ireland" not in location.lower() and "Ireland" in regions:
        return f"{location}, Ireland"
    return location or region_text


def _employer(doc: dict) -> str | None:
    organisation = doc.get("organisation")
    if isinstance(organisation, dict) and organisation.get("title"):
        return organisation["title"]
    return doc.get("sourceOrganisationName")


def _date(value):
    try:
        return date_parser.parse(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


def parse(doc: dict) -> RawJob | None:
    nid = doc.get("nid")
    title = doc.get("title")
    if not nid or not title:
        return None
    kinds = doc.get("opportunityType") or []
    return RawJob(
        source_job_id=str(nid),
        title=html.unescape(title),
        url=ORIGIN + (doc.get("path") or f"/node/{nid}"),
        location_raw=_location(doc),
        description=doc.get("body"),
        department=", ".join(kinds) if isinstance(kinds, list) else str(kinds),
        company_name=_employer(doc),
    )


class GradIrelandAdapter(BaseAdapter):
    name = "gradireland"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: dict[str, RawJob] = {}
        total = None
        for page in range(MAX_PAGES):
            response = client.post(SEARCH, json=query(page * PAGE_SIZE), headers=HEADERS)
            response.raise_for_status()
            search = (response.json() or {}).get("search") or {}
            documents = search.get("documents") or []
            total = search.get("result_count", total)
            if page == 0 and not documents:
                raise ValueError("gradireland search returned nothing; the query was refused")
            for doc in documents:
                job = parse(doc)
                if job:
                    jobs.setdefault(job.source_job_id, job)
            if len(documents) < PAGE_SIZE:
                return list(jobs.values())
            self.polite_pause()
        return PartialJobs(jobs.values())


register(GradIrelandAdapter())
