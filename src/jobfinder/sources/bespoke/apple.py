"""Apple careers adapter.

Apple runs its own platform. Its JSON API wants a session token, but the search page is
rendered on the server with the same results embedded as hydration data:

    https://jobs.apple.com/en-ie/search?location=ireland-IRL&page=2

    window.__staticRouterHydrationData = JSON.parse("...")
        -> loaderData.search.{totalRecords, searchResults[20]}

Two behaviours shape the loop. A page sometimes comes back with no results and a total
of 0 although the same page is full a moment later, so a short page before the stated
total is retried and, if it stays short, the fetch fails rather than returning a list
that would close the roles it missed. And the location filter is a slug, so an unknown
one could silently return the whole world; a total far above Ireland's is refused.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

SEARCH_URL = "https://jobs.apple.com/en-ie/search"
DETAIL_URL = "https://jobs.apple.com/en-ie/details/{id}/{slug}"
PAGE_SIZE = 20
MAX_PAGES = 50
PAGE_RETRIES = 3
IMPLAUSIBLE_TOTAL = 1500

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IE,en;q=0.9",
}

_HYDRATION = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', re.S
)


def parse_search(html: str) -> dict:
    """The search loader's data from a rendered results page."""
    match = _HYDRATION.search(html)
    if not match:
        raise ValueError("apple search page carried no hydration data - markup changed")
    # The payload is a JSON document inside a JavaScript string literal.
    data = json.loads(json.loads(f'"{match.group(1)}"'))
    search = (data.get("loaderData") or {}).get("search")
    if not isinstance(search, dict):
        raise ValueError("apple hydration data has no search results")
    return search


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(value).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _place(location: dict) -> str | None:
    parts = [location.get("name"), location.get("countryName")]
    text = ", ".join(p for p in parts if p)
    return text or None


class AppleAdapter(BaseAdapter):
    name = "apple"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        location = slug or "ireland-IRL"
        found: dict[str, dict] = {}
        total: int | None = None

        for page in range(1, MAX_PAGES + 1):
            search = self._page(client, location, page, expect=total)
            if total is None:
                total = int(search.get("totalRecords") or 0)
                if total >= IMPLAUSIBLE_TOTAL:
                    raise ValueError(
                        f"apple location filter {location!r} appears to have been ignored "
                        f"({total} roles)"
                    )
            for item in search.get("searchResults") or []:
                key = item.get("id") or item.get("positionId")
                if key:
                    found.setdefault(str(key), item)
            if len(found) >= total or not search.get("searchResults"):
                break
            self.polite_pause()

        if total and len(found) < total:
            raise ValueError(f"apple returned {len(found)} of {total} roles")
        return [self._build(item) for item in found.values()]

    def _page(
        self, client: httpx.Client, location: str, page: int, *, expect: int | None
    ) -> dict:
        for attempt in range(1, PAGE_RETRIES + 1):
            response = client.get(
                SEARCH_URL,
                params={"location": location, "page": page},
                headers=BROWSER_HEADERS,
            )
            response.raise_for_status()
            search = parse_search(response.text)
            # A later page answering "0 roles" is the transient empty render, not the end.
            if expect is None or search.get("totalRecords") or search.get("searchResults"):
                return search
            logger.info("apple page %d came back empty (attempt %d)", page, attempt)
            time.sleep(attempt)
        raise ValueError(f"apple page {page} stayed empty although {expect} roles exist")

    @staticmethod
    def _build(item: dict) -> RawJob:
        places = [p for p in (_place(loc) for loc in item.get("locations") or []) if p]
        team = item.get("team") or {}
        return RawJob(
            source_job_id=str(item.get("id") or item.get("positionId")),
            title=item.get("postingTitle") or "",
            url=DETAIL_URL.format(
                id=item.get("id") or item.get("positionId"),
                slug=item.get("transformedPostingTitle") or "",
            ),
            location_raw=places[0] if places else None,
            extra_locations=places[1:],
            description=item.get("jobSummary"),
            posted_at=_parse_date(item.get("postDateInGMT")),
            department=team.get("teamName"),
        )


register(AppleAdapter())
