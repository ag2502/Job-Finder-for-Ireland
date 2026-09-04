"""Amazon careers adapter.

Amazon runs its own platform rather than a standard ATS, but publishes a clean public
JSON search endpoint:

    https://www.amazon.jobs/en/search.json?country=IRL&result_limit=100&offset=0

The parameter name matters more than it looks. `loc_query="Dublin, Ireland"` is a fuzzy
match that happily returns Tempe, Arizona and reports 10,000 hits, and the plural
`country[]` form is ignored entirely with the same 10,000-hit symptom. Only the singular
`country=IRL` actually filters, narrowing to ~206 genuinely Irish postings.

That failure mode is worth remembering: the endpoint does not reject an unsupported
filter, it silently returns everything. The hit count is the tell.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.amazon.jobs/en/search.json"
PAGE_SIZE = 100
MAX_PAGES = 20

# Guards against the silent-no-filter behaviour described above.
IMPLAUSIBLE_HIT_COUNT = 5000

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class AmazonAdapter(BaseAdapter):
    name = "amazon"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        # `slug` carries the ISO-3 country code so the adapter is reusable.
        country = slug or "IRL"
        jobs: list[RawJob] = []
        offset = 0

        for page in range(MAX_PAGES):
            response = client.get(
                SEARCH_URL,
                params={
                    "country": country,
                    "result_limit": PAGE_SIZE,
                    "offset": offset,
                    "sort": "recent",
                },
                headers=BROWSER_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()

            hits = payload.get("hits") or 0
            if page == 0 and hits >= IMPLAUSIBLE_HIT_COUNT:
                # The country filter was not honoured; refuse rather than import the
                # entire global board as Irish roles.
                raise ValueError(
                    f"amazon country filter appears to have been ignored ({hits} hits)"
                )

            batch = payload.get("jobs") or []
            if not batch:
                break

            for item in batch:
                job_id = item.get("id_icims") or item.get("id")
                if not job_id:
                    continue

                path = item.get("job_path") or ""
                jobs.append(
                    RawJob(
                        source_job_id=str(job_id),
                        title=item.get("title") or "",
                        url=f"https://www.amazon.jobs{path}" if path else "",
                        location_raw=item.get("normalized_location") or item.get("location"),
                        description=item.get("description") or item.get("description_short"),
                        posted_at=_parse_date(item.get("posted_date")),
                        department=item.get("job_category") or item.get("team"),
                    )
                )

            offset += PAGE_SIZE
            if offset >= hits:
                break
            self.polite_pause()

        return jobs


register(AmazonAdapter())
