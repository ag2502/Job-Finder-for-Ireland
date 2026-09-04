"""Ashby job board adapter.

    https://api.ashbyhq.com/posting-api/job-board/{slug}

Ashby models multi-office roles explicitly through `secondaryLocations`. Reading only
the primary `location` field drops any role whose Dublin office is listed second, which
is a silent under-count rather than a visible error — so every secondary location is
expanded and fed to the matcher alongside the primary.

`isListed` marks whether a posting is actually public; unlisted rows are excluded.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

API_ROOT = "https://api.ashbyhq.com/posting-api/job-board"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


class AshbyAdapter(BaseAdapter):
    name = "ashby"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"{API_ROOT}/{slug}")
        response.raise_for_status()
        payload = response.json()

        jobs: list[RawJob] = []
        for item in payload.get("jobs", []):
            if item.get("isListed") is False:
                continue

            job_id = item.get("id")
            if not job_id:
                continue

            secondary = [
                entry.get("location")
                for entry in item.get("secondaryLocations") or []
                if isinstance(entry, dict) and entry.get("location")
            ]

            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("title") or "",
                    url=item.get("jobUrl") or item.get("applyUrl") or "",
                    location_raw=item.get("location"),
                    description=item.get("descriptionPlain")
                    or item.get("descriptionHtml"),
                    posted_at=_parse_date(item.get("publishedAt")),
                    department=item.get("department") or item.get("team"),
                    extra_locations=secondary,
                )
            )
        return jobs


register(AshbyAdapter())
