"""Workable job board adapter.

    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
         {}                       # first page
         {"token": "<nextPage>"}  # subsequent pages

Location arrives structured rather than as free text — ``{"city": ..., "country": ...}``
— which is a rarity worth exploiting: the city and country are unambiguous, so a Dublin
posting cannot be confused with Dublin, Ohio. The parts are joined into a raw string for
the normalizer, which then has an easy job.

`locations` (plural) is the per-posting multi-office list, so it is expanded like
Lever's `allLocations` and Ashby's `secondaryLocations`.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

API_ROOT = "https://apply.workable.com/api/v3/accounts"
MAX_PAGES = 40


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def format_location(location: dict | None) -> str | None:
    """Join a structured Workable location into "City, Region, Country"."""
    if not isinstance(location, dict):
        return None
    parts = [
        location.get("city"),
        location.get("region"),
        location.get("country"),
    ]
    # Region often duplicates the city (e.g. New York / New York); drop repeats.
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


class WorkableAdapter(BaseAdapter):
    name = "workable"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: list[RawJob] = []
        token: str | None = None

        for _ in range(MAX_PAGES):
            body: dict = {"token": token} if token else {}
            response = client.post(
                f"{API_ROOT}/{slug}/jobs",
                json=body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()

            results = payload.get("results") or []
            if not results:
                break

            for item in results:
                shortcode = item.get("shortcode") or item.get("id")
                if not shortcode:
                    continue
                if item.get("isInternal") or item.get("state") not in (None, "published"):
                    continue

                primary = format_location(item.get("location"))
                extras = [
                    formatted
                    for formatted in (
                        format_location(loc)
                        for loc in item.get("locations") or []
                        if not (isinstance(loc, dict) and loc.get("hidden"))
                    )
                    if formatted and formatted != primary
                ]

                departments = item.get("department") or []
                jobs.append(
                    RawJob(
                        source_job_id=str(shortcode),
                        title=item.get("title") or "",
                        url=f"https://apply.workable.com/{slug}/j/{shortcode}/",
                        location_raw=primary,
                        posted_at=_parse_date(item.get("published")),
                        department=departments[0] if departments else None,
                        extra_locations=extras,
                    )
                )

            token = payload.get("nextPage")
            if not token:
                break
            self.polite_pause()

        return jobs


register(WorkableAdapter())
