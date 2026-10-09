"""People First (MHR) job boards, at {tenant}.jobs.people-first.com.

The board is a single-page app over a public JSON API that wants the tenant named in a
`tenantcode` header. The board profile names the organisation, its `availableJobs`
link lists every open job (up to a thousand a page), and each job's details carry the
advert. Dublin Simon Community recruits this way, much of it part-time project and
support work.

The slug is the tenant, the board's subdomain.
"""

from __future__ import annotations

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

PAGE_SIZE = 200
MAX_DESCRIPTIONS = 150


def _place(item: dict) -> str | None:
    """'Wicklow- WCC\\r\\nWicklow\\r\\nIreland' -> 'Wicklow, Ireland'."""
    lines = [line.strip() for line in (item.get("formattedAddress") or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return ", ".join(dict.fromkeys(lines[1:]))
    return item.get("location") or (lines[0] if lines else None)


class PeopleFirstAdapter(BaseAdapter):
    name = "peoplefirst"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant = slug.strip().lower()
        base = f"https://{tenant}.jobs.people-first.com"
        headers = {"tenantcode": tenant, "Accept": "application/json"}

        profile = client.get(f"{base}/api/v1/recruitment/jobboard/profile", headers=headers)
        profile.raise_for_status()
        links = profile.json()["data"]["jobBoardProfile"]["_links"]
        listed = client.get(
            f"{base}/api/v1/{links['availableJobs']['href']}",
            params={"page[limit]": PAGE_SIZE},
            headers=headers,
        )
        listed.raise_for_status()
        payload = listed.json()
        items = (payload.get("data") or {}).get("availablejobs") or []
        meta = payload.get("meta") or {}

        jobs: list[RawJob] = []
        for index, item in enumerate(items):
            if item.get("internalOnly") or not item.get("jobId") or not item.get("title"):
                continue
            job_id = item["jobId"]
            description = None
            if index < MAX_DESCRIPTIONS:
                try:
                    detail = client.get(f"{base}/api/v1/recruitment/jobdetails/{job_id}", headers=headers)
                    detail.raise_for_status()
                    description = (detail.json().get("data") or {}).get("jobDetails", {}).get("jobDescription")
                except (httpx.HTTPError, ValueError):
                    pass
                self.polite_pause()
            start = item.get("startDate")
            try:
                posted_at = date_parser.parse(start) if start else None
            except (ValueError, OverflowError):
                posted_at = None
            jobs.append(RawJob(
                source_job_id=job_id,
                title=item["title"].strip(),
                url=f"{base}/jobs/details/recruitment%2Fjobdetails%2F{job_id}",
                location_raw=_place(item),
                description=description,
                posted_at=posted_at,
            ))

        if int(meta.get("totalPages") or 1) > 1:
            return PartialJobs(jobs)
        return jobs


register(PeopleFirstAdapter())
