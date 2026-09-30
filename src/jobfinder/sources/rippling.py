"""Rippling ATS adapter.

    GET https://ats.rippling.com/api/v2/board/{slug}/jobs?page={n}&pageSize=100
    GET https://ats.rippling.com/api/v2/board/{slug}/jobs/{id}

Rippling's hiring product, used by Rippling itself and a long tail of startups, serves
each company's board as public JSON. The list repeats a role once for every office it
is open in (Rippling's own 641 rows are about 330 roles), each row carrying one
location with its country code, so rows are merged by id and every office kept: the
first as the location, the rest as extra offices. `totalPages` says where the list
ends.

The detail call carries the advert, split into a `company` and a `role` section, and
the posting date. Boards are global, so it is made only for a role with an Irish
office.

The slug is the board name in the careers URL: `ats.rippling.com/{slug}/jobs`.
"""

from __future__ import annotations

import httpx
from dateutil import parser as dateparser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

API = "https://ats.rippling.com/api/v2/board"
PAGE_SIZE = 100
MAX_PAGES = 40


def _place(location: dict) -> str | None:
    name = location.get("name")
    country = location.get("country")
    if name and country and country not in name:
        return f"{name}, {country}"
    return name or country


class RipplingAdapter(BaseAdapter):
    name = "rippling"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        roles: dict[str, dict] = {}
        offices: dict[str, list[str]] = {}
        irish: set[str] = set()
        complete = False
        for page in range(MAX_PAGES):
            response = client.get(
                f"{API}/{slug}/jobs", params={"page": page, "pageSize": PAGE_SIZE}
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("items") or []:
                job_id = item.get("id")
                if not job_id:
                    continue
                roles.setdefault(job_id, item)
                for location in item.get("locations") or []:
                    place = _place(location)
                    if place and place not in offices.setdefault(job_id, []):
                        offices[job_id].append(place)
                    if location.get("countryCode") == "IE":
                        irish.add(job_id)
            if page + 1 >= (payload.get("totalPages") or 0):
                complete = True
                break
            self.polite_pause()

        jobs: list[RawJob] = []
        for job_id, item in roles.items():
            description, posted = None, None
            if job_id in irish:
                try:
                    detail = client.get(f"{API}/{slug}/jobs/{job_id}")
                    detail.raise_for_status()
                    data = detail.json()
                    parts = data.get("description") or {}
                    if isinstance(parts, dict):
                        description = "\n".join(p for p in (parts.get("role"), parts.get("company")) if p)
                    else:
                        description = str(parts) or None
                    if data.get("createdOn"):
                        posted = dateparser.parse(data["createdOn"])
                except (httpx.HTTPError, ValueError):
                    # The list names the role and its offices; the advert is a bonus.
                    pass
                self.polite_pause()
            places = offices.get(job_id) or [None]
            jobs.append(RawJob(
                source_job_id=job_id,
                title=item.get("name") or "",
                url=item.get("url") or f"https://ats.rippling.com/{slug}/jobs/{job_id}",
                location_raw=places[0],
                extra_locations=places[1:],
                description=description or None,
                posted_at=posted,
                department=(item.get("department") or {}).get("name"),
            ))
        return jobs if complete else PartialJobs(jobs)


register(RipplingAdapter())
