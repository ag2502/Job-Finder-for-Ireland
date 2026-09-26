"""HiBob careers-site adapter.

HiBob's hosted careers sites ({tenant}.careers.hibob.com) load every open role from one
endpoint, which answers once the request names the tenant in a header, as the site's
own page does:

    GET https://{tenant}.careers.hibob.com/api/job-ad      companyidentifier: {tenant}
        -> {jobAdDetails: [{id, title, site, country, department, publishedAt,
                            workspaceType, responsibilities, requirements, benefits}]}

Nostra, Corlytics and Conscia (formerly PlanNet21) recruit this way.
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
        return date_parser.isoparse(value)
    except (ValueError, TypeError, OverflowError):
        return None


class HiBobAdapter(BaseAdapter):
    name = "hibob"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(
            f"https://{slug}.careers.hibob.com/api/job-ad",
            headers={"companyidentifier": slug, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobAdDetails"), list):
            raise ValueError(f"unexpected HiBob payload for {slug!r}")

        jobs: list[RawJob] = []
        for item in payload["jobAdDetails"]:
            if not item.get("id") or not item.get("title"):
                continue
            place = ", ".join(part for part in (item.get("site"), item.get("country")) if part) or None
            if place and (item.get("workspaceTypeId") or "").lower() == "remote":
                place = f"{place} (Remote)"
            description = "".join(
                section for section in (item.get("responsibilities"), item.get("requirements"), item.get("benefits"))
                if isinstance(section, str) and section.strip()
            )
            jobs.append(RawJob(
                source_job_id=str(item["id"]),
                title=item["title"].strip(),
                url=f"https://{slug}.careers.hibob.com/jobs/{item['id']}",
                location_raw=place,
                description=description or None,
                posted_at=_parse_date(item.get("publishedAt")),
                department=item.get("department"),
            ))
        return jobs


register(HiBobAdapter())
