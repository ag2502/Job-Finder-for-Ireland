"""HireHive job board adapter.

    https://{slug}.hirehive.com/api/v2/jobs?page={n}

HireHive is a Cork-built ATS used mostly by Irish SMEs and scale-ups, which is why it
turned up behind careers pages in the blocked queue that carried no other fingerprint.
The board API is public and paginated, and each posting carries a free-text `location`
plus a structured country, so an Irish role is unambiguous even when the city is not.

The API does not name the board's owner, so registration relies on the slug resembling
the company (see `registry/bulk_detect.py`).
"""

from __future__ import annotations

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

# A board with more pages than this is a sample, and is reported as one.
MAX_PAGES = 10


def _location(item: dict) -> str | None:
    city = (item.get("location") or "").strip()
    country = ((item.get("country") or {}).get("name") or "").strip()
    parts = [part for part in (city, country) if part]
    # "Dublin, Ireland" rather than "Dublin, Dublin, Ireland" when the city already names it.
    if len(parts) == 2 and parts[1].lower() in parts[0].lower():
        parts = parts[:1]
    return ", ".join(parts) or None


class HireHiveAdapter(BaseAdapter):
    name = "hirehive"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: list[RawJob] = []
        page = 1
        while True:
            response = client.get(
                f"https://{slug}.hirehive.com/api/v2/jobs",
                params={"page": page, "page_size": 30},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "items" not in payload:
                raise ValueError(f"unexpected HireHive payload for {slug!r}")

            for item in payload["items"]:
                job_id = item.get("id")
                if not job_id:
                    continue
                description = item.get("description") or {}
                published = item.get("published_date")
                jobs.append(
                    RawJob(
                        source_job_id=str(job_id),
                        title=item.get("title") or "",
                        url=item.get("hosted_url") or f"https://{slug}.hirehive.com/",
                        location_raw=_location(item),
                        description=description.get("html") or description.get("text"),
                        posted_at=date_parser.parse(published) if published else None,
                        department=(item.get("category") or {}).get("name")
                        if isinstance(item.get("category"), dict)
                        else item.get("category"),
                    )
                )

            if not (payload.get("meta") or {}).get("has_next_page"):
                return jobs
            if page >= MAX_PAGES:
                return PartialJobs(jobs)
            page += 1
            self.polite_pause()


register(HireHiveAdapter())
