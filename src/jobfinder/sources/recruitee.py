"""Recruitee job board adapter.

    https://{slug}.recruitee.com/api/offers/

Recruitee's careers-site API is unauthenticated and returns the full advert in the list
response, so one request covers a whole board — no detail fetch, unlike SmartRecruiters
and Workday.

An unknown tenant answers 404, which the base adapter turns into FAILED. That is the
behaviour to want: the reconciler then leaves the source's jobs alone instead of reading
an empty board as "everything closed".

`locations` is a genuine per-posting list — the same semantics as Ashby's
`secondaryLocations` and Lever's `allLocations`, not Greenhouse's company-wide
`offices` — so it is expanded. `description` and `requirements` are separate HTML
fields and both matter for matching, so they are concatenated.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def format_location(entry: dict) -> str | None:
    """Join one Recruitee location object into "City, State, Country"."""
    if not isinstance(entry, dict):
        return None
    parts = [entry.get("city"), entry.get("state"), entry.get("country")]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


class RecruiteeAdapter(BaseAdapter):
    name = "recruitee"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"https://{slug}.recruitee.com/api/offers/")
        response.raise_for_status()
        payload = response.json()

        offers = payload.get("offers")
        if not isinstance(offers, list):
            raise ValueError(
                f"unexpected Recruitee payload for {slug!r}: {type(offers).__name__}"
            )

        jobs: list[RawJob] = []
        for item in offers:
            job_id = item.get("id")
            if not job_id:
                continue
            # Drafts and closed roles share the endpoint with live ones.
            if item.get("status") not in (None, "published"):
                continue

            # `location` is already a formatted string; the structured parts are the
            # fallback for boards that leave it empty.
            primary = item.get("location") or format_location(item)

            extras = [
                formatted
                for formatted in (
                    format_location(entry) for entry in item.get("locations") or []
                )
                if formatted and formatted != primary
            ]

            description = "\n\n".join(
                chunk
                for chunk in (item.get("description"), item.get("requirements"))
                if chunk
            )

            offer_slug = item.get("slug")
            url = item.get("careers_url") or (
                f"https://{slug}.recruitee.com/o/{offer_slug}" if offer_slug else ""
            )

            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("title") or "",
                    url=url,
                    location_raw=primary,
                    description=description or None,
                    posted_at=_parse_date(item.get("published_at") or item.get("created_at")),
                    department=item.get("department"),
                    extra_locations=extras,
                )
            )
        return jobs


register(RecruiteeAdapter())
