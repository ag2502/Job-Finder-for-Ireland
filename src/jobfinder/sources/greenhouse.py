"""Greenhouse job board adapter.

Greenhouse exposes every customer's board publicly so it can be embedded on the
customer's own marketing site:

    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Two details that are easy to get wrong:

* `content` arrives HTML-entity-encoded (``&lt;div&gt;``), so it needs unescaping before
  it is worth indexing.
* A posting carries both `location.name` and an `offices` list, and it is tempting to
  treat `offices` as extra locations for the role. It is not. Many companies attach the
  full company office list to every posting — Intercom tags every job with
  ``['Dublin, Ireland', 'London, England']`` — so trusting it marks every London role as
  Dublin. `offices` is therefore consulted only when the posting has no location of its
  own, where it is the best signal available rather than a misleading one.

  This is the opposite of Ashby, whose `secondaryLocations` really is per-posting.
"""

from __future__ import annotations

import html
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

API_ROOT = "https://boards-api.greenhouse.io/v1/boards"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


class GreenhouseAdapter(BaseAdapter):
    name = "greenhouse"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"{API_ROOT}/{slug}/jobs", params={"content": "true"})
        response.raise_for_status()
        payload = response.json()

        jobs: list[RawJob] = []
        for item in payload.get("jobs", []):
            job_id = item.get("id")
            if job_id is None:
                continue

            location = (item.get("location") or {}).get("name")

            # Only fall back to the company office list when the posting itself gives
            # no location. See the module docstring: `offices` is company-wide.
            extra: list[str] = []
            if not (location and location.strip()):
                extra = [
                    office.get("name")
                    for office in item.get("offices") or []
                    if office.get("name")
                ]

            content = item.get("content")
            if content:
                content = html.unescape(content)

            departments = [
                d.get("name") for d in item.get("departments") or [] if d.get("name")
            ]

            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("title") or "",
                    url=item.get("absolute_url") or "",
                    location_raw=location,
                    description=content,
                    posted_at=_parse_date(
                        item.get("first_published") or item.get("updated_at")
                    ),
                    department=departments[0] if departments else None,
                    extra_locations=extra,
                )
            )
        return jobs


register(GreenhouseAdapter())
