"""Lever job board adapter.

    https://api.lever.co/v0/postings/{slug}?mode=json

Returns a flat JSON array. `categories.allLocations` is a genuine per-posting list — the
same semantics as Ashby's `secondaryLocations`, and unlike Greenhouse's company-wide
`offices` — so it is safe to expand and necessary to avoid dropping roles whose Dublin
office is listed second.

`createdAt` is epoch milliseconds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register

API_ROOT = "https://api.lever.co/v0/postings"


def _from_epoch_ms(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class LeverAdapter(BaseAdapter):
    name = "lever"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"{API_ROOT}/{slug}", params={"mode": "json"})
        response.raise_for_status()
        payload = response.json()

        # A missing board returns a JSON object describing the error rather than a list.
        if not isinstance(payload, list):
            raise ValueError(f"unexpected Lever payload for {slug!r}: {type(payload).__name__}")

        jobs: list[RawJob] = []
        for item in payload:
            job_id = item.get("id")
            if not job_id:
                continue

            categories = item.get("categories") or {}
            primary = categories.get("location")
            all_locations = [
                loc for loc in categories.get("allLocations") or [] if loc and loc != primary
            ]

            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("text") or "",
                    url=item.get("hostedUrl") or item.get("applyUrl") or "",
                    location_raw=primary,
                    description=item.get("descriptionPlain") or item.get("description"),
                    posted_at=_from_epoch_ms(item.get("createdAt")),
                    department=categories.get("department") or categories.get("team"),
                    extra_locations=all_locations,
                )
            )
        return jobs


register(LeverAdapter())
