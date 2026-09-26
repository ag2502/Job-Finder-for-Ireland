"""Rezoomo adapter.

Rezoomo is an Irish recruitment platform used widely in retail, hospitality and
healthcare. A company's page at rezoomo.com/company/{slug}/jobs/ is filled by one form
post that returns everything the page shows, the open roles included:

    POST https://www.rezoomo.com/index.cfm
         action=api.front.company.onMount&companyUrl={slug}
         -> data.companyJobs[{id, name, location, description, postDate, ...}]

The whole list arrives at once, so there is no paging to get wrong. A company with no
open roles is answered with an empty list, which is taken as the answer; an answer with
no `companyJobs` key at all, or a failure flag, is not.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

API_URL = "https://www.rezoomo.com/index.cfm"
JOB_URL = "https://www.rezoomo.com/job/{id}/"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value.replace(",", ""))
    except (ValueError, TypeError, OverflowError):
        return None


class RezoomoAdapter(BaseAdapter):
    name = "rezoomo"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.post(
            API_URL,
            files={
                "companySubdomain": (None, ""),
                "preview": (None, "false"),
                "action": (None, "api.front.company.onMount"),
                "companyUrl": (None, slug),
                "isExternal": (None, "false"),
            },
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not payload.get("success") or not isinstance(data, dict) or "companyJobs" not in data:
            raise ValueError(f"Rezoomo returned no job list for {slug!r}")

        jobs: list[RawJob] = []
        for item in data.get("companyJobs") or []:
            if not item.get("id") or item.get("isPublished") is False:
                continue
            if "public" not in (item.get("scope") or ["public"]):
                continue
            salary = item.get("salary")
            description = item.get("description") or ""
            if salary and salary != "Not Disclosed":
                description = f"<p>Salary: {salary}</p>{description}"
            jobs.append(
                RawJob(
                    source_job_id=str(item["id"]),
                    title=(item.get("name") or "").strip(),
                    url=JOB_URL.format(id=item["id"]),
                    location_raw=item.get("location") or item.get("loc"),
                    description=description or None,
                    posted_at=_parse_date(item.get("postDate")),
                )
            )
        return jobs


register(RezoomoAdapter())
