"""SmartRecruiters job board adapter.

    https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=ie

The public Posting API needs no key, and it is the only Tier 1 board with a native
country facet. That is worth using rather than filtering client-side: BoschGroup carries
4,800 live postings globally and two in Ireland, so `country=ie` turns a 49-page crawl
into a single request. The same reasoning drives Workday's `searchText` filter.

Two traps.

**An unknown tenant returns HTTP 200 with an empty list, not a 404.** Every other board
in this project answers a typo with an error; SmartRecruiters answers it with

    {"offset": 0, "limit": 100, "totalFound": 0, "content": []}

which is indistinguishable from a real company that has closed its last vacancy. A
mistyped slug would therefore register as a healthy source that reports zero jobs
forever. `verify_slug` exists so registration can reject the slug up front — the one
moment the distinction is still checkable, because a company being onboarded is expected
to have postings somewhere. The reconciler's volume-drop guard covers the other
direction (an established source collapsing to zero), but it cannot help a source that
was never right to begin with.

**Descriptions are absent from the list response.** They live on the per-posting detail
endpoint, one request each. Because the list is already filtered to Ireland, that is a
handful of requests rather than thousands — the same list-then-detail shape Workday uses,
and for the same reason.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, build_client, register

logger = logging.getLogger(__name__)

API_ROOT = "https://api.smartrecruiters.com/v1/companies"

PAGE_SIZE = 100
MAX_PAGES = 50

# The country facet takes a lowercase ISO 3166-1 alpha-2 code.
COUNTRY = "ie"

# Section order follows how the ad reads on the page, so the indexed text opens with the
# role rather than with boilerplate about the employer.
SECTION_ORDER = (
    "jobDescription",
    "qualifications",
    "additionalInformation",
    "companyDescription",
)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def format_location(location: dict | None) -> str | None:
    """Build a raw location string from SmartRecruiters' structured location.

    `fullLocation` is preferred when present but is frequently malformed — Bosch returns
    ``"Ho Chi Minh, , Vietnam"`` with an empty region — so the parts are rejoined from
    city/region/country instead, which the normalizer can read unambiguously.
    """
    if not isinstance(location, dict):
        return None

    parts = [location.get("city"), location.get("region")]
    country = location.get("country")
    if isinstance(country, str) and country:
        # The API returns "ie"; upper-casing it keeps the normalizer from reading a
        # two-letter lowercase token as a word.
        parts.append(country.upper())

    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


def _description(detail: dict) -> str | None:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    chunks: list[str] = []
    for key in SECTION_ORDER:
        section = sections.get(key)
        if isinstance(section, dict):
            text = section.get("text")
            if text:
                chunks.append(text)
    return "\n\n".join(chunks) or None


def verify_slug(slug: str, client: httpx.Client | None = None) -> bool:
    """Is this an existing SmartRecruiters tenant?

    Called at registration time, not at crawl time. A tenant with no postings anywhere
    is treated as not existing, because from the outside the two are the same response
    and a slug that has never returned a posting is far more likely to be a typo.
    """
    owns = client is None
    client = client or build_client()
    try:
        response = client.get(f"{API_ROOT}/{slug}/postings", params={"limit": 1})
        if response.status_code != 200:
            return False
        return bool(response.json().get("totalFound"))
    except (httpx.HTTPError, ValueError):
        return False
    finally:
        if owns:
            client.close()


class SmartRecruitersAdapter(BaseAdapter):
    name = "smartrecruiters"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        postings = self._list_irish_postings(slug, client)

        jobs: list[RawJob] = []
        for item in postings:
            job_id = item.get("id")
            if not job_id:
                continue

            detail = self._detail(slug, str(job_id), client)
            source = detail or item

            location = source.get("location") or {}
            url = (
                source.get("postingUrl")
                or source.get("applyUrl")
                or f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
            )

            department = source.get("department") or {}
            function = source.get("function") or {}

            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=source.get("name") or "",
                    url=url,
                    location_raw=format_location(location),
                    description=_description(source) if detail else None,
                    posted_at=_parse_date(source.get("releasedDate")),
                    department=department.get("label") or function.get("label"),
                )
            )
        return jobs

    def _list_irish_postings(self, slug: str, client: httpx.Client) -> list[dict]:
        """Page through the Ireland-filtered posting list."""
        postings: list[dict] = []

        for page in range(MAX_PAGES):
            response = client.get(
                f"{API_ROOT}/{slug}/postings",
                params={
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "country": COUNTRY,
                },
            )
            response.raise_for_status()
            payload = response.json()

            content = payload.get("content") or []
            if not content:
                break
            postings.extend(content)

            if len(postings) >= (payload.get("totalFound") or 0):
                break
            self.polite_pause()

        return postings

    def _detail(self, slug: str, job_id: str, client: httpx.Client) -> dict | None:
        """Fetch one posting's full ad.

        A detail failure degrades to the list row rather than failing the whole source:
        the job's identity, title and location are already known, and losing its
        description costs ranking quality but not the posting itself.
        """
        try:
            response = client.get(f"{API_ROOT}/{slug}/postings/{job_id}")
            response.raise_for_status()
            self.polite_pause()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("smartrecruiters detail %s/%s failed: %r", slug, job_id, exc)
            return None


register(SmartRecruitersAdapter())
