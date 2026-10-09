"""Lidl's own job site, which every Lidl country runs on the same platform.

    https://jobs.lidl.ie/api/v1/search?general={"page":1,"resultsPerPage":100,...}

The site's search calls this endpoint for its result list, and each record is the whole
advert: title, the store's address with its town, the contract type ("Full Time",
"Part Time") and the full description. One request at a page size of a hundred covers
Ireland, where Lidl has about 80 vacancies open at a time. Behind it the applications
go to SuccessFactors, but the career site there is a login-first portal that lists
nothing, which is why detection's SuccessFactors match read no jobs.

The slug is the country site's host, `jobs.lidl.ie`.
"""

from __future__ import annotations

import json

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

PAGE_SIZE = 100
MAX_PAGES = 20


def to_raw_job(item: dict) -> RawJob | None:
    job_id = item.get("requisitionId") or item.get("postingId")
    title = (item.get("title") or "").strip()
    if not job_id or not title:
        return None
    place = item.get("location") or {}
    town = (place.get("city") or "").strip()
    country = (place.get("country") or "").strip()
    location = ", ".join(filter(None, [town, "Ireland" if country == "IE" else country])) or None
    description = "\n".join(filter(None, [item.get("descHeader"), item.get("descResponsibilities")]))
    posted = item.get("onlineFrom")
    try:
        posted_at = date_parser.parse(posted) if posted else None
    except (ValueError, OverflowError):
        posted_at = None
    return RawJob(
        source_job_id=str(job_id),
        title=title,
        url=item.get("jobDetailUrl") or item.get("recruitingUrlSF") or "",
        location_raw=location,
        description=description or None,
        posted_at=posted_at,
        department=item.get("employmentArea") or None,
        employment_type=item.get("contractType") or item.get("workingModel") or None,
    )


class LidlAdapter(BaseAdapter):
    name = "lidl"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host = slug.strip().removeprefix("https://").strip("/")
        jobs: dict[str, RawJob] = {}
        total = 0
        for page in range(1, MAX_PAGES + 1):
            query = {"page": page, "resultsPerPage": PAGE_SIZE, "sortField": "", "sortOrder": "asc"}
            response = client.get(f"https://{host}/api/v1/search", params={"general": json.dumps(query)})
            response.raise_for_status()
            payload = response.json()
            items = payload.get("jobs") or []
            total = int((payload.get("meta") or {}).get("totalCount") or 0)
            for item in items:
                job = to_raw_job(item)
                if job:
                    jobs.setdefault(job.source_job_id, job)
            if len(items) < PAGE_SIZE or len(jobs) >= total:
                break
            self.polite_pause()
        else:
            return PartialJobs(jobs.values())
        if total and len(jobs) < total:
            return PartialJobs(jobs.values())
        return list(jobs.values())


register(LidlAdapter())
