"""SeeMeHired, an ATS for care homes, hotels and pubs in Ireland and the UK.

    https://api.seemehired.com/public/jobs/search/paginated?page={n}&limit=200

Its customers embed a vacancy frame in their own careers pages (Louis Fitzgerald's
pubs, Silver Stream's nursing homes), and the frame's URL is closed by robots.txt. The
public search API behind seemehired.com/jobs is not: it lists every live job on the
platform, about 1,700, each naming its employer, its town and country, and its contract
type ("Part time"). The whole list is read in nine requests and the Irish jobs kept,
which reaches every Irish SeeMeHired customer at once, filed under its own name.

The site's llms.txt points automated readers at its job listings and their structured
data, and the API host's robots.txt allows everything.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

SEARCH = "https://api.seemehired.com/public/jobs/search/paginated"
JOB_URL = "https://seemehired.com/jobs/{id}"
PAGE_SIZE = 200
MAX_PAGES = 40
IRELAND = "Ireland"


def _when(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def to_raw_job(item: dict) -> RawJob | None:
    """An Irish job as a RawJob, or None for anything else."""
    place = item.get("location") or {}
    if place.get("countryName") != IRELAND or item.get("isJobInternal") or item.get("isActive") is False:
        return None
    job_id, title = item.get("id"), (item.get("title") or "").strip()
    if not job_id or not title:
        return None
    town = (place.get("cityName") or "").strip()
    location = f"{town}, Ireland" if town and town.lower() != "ireland" else (
        place.get("fullAddress") or "Ireland"
    )
    contract = item.get("contractType") or {}
    return RawJob(
        source_job_id=str(job_id),
        title=title,
        url=JOB_URL.format(id=job_id),
        location_raw=location,
        posted_at=_when(item.get("publishedAt")),
        employment_type=contract.get("name") if isinstance(contract, dict) else None,
        company_name=(item.get("employer") or "").strip() or None,
    )


class SeeMeHiredAdapter(BaseAdapter):
    """Every Irish job on SeeMeHired. The slug is unused beyond naming the source."""

    name = "seemehired"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: dict[str, RawJob] = {}
        seen = total = 0
        for page in range(1, MAX_PAGES + 1):
            response = client.get(SEARCH, params={"page": page, "limit": PAGE_SIZE})
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") or []
            total = int(payload.get("total") or 0)
            seen += len(items)
            for item in items:
                job = to_raw_job(item)
                if job:
                    jobs.setdefault(job.source_job_id, job)
            if not items or seen >= total:
                break
            self.polite_pause()
        else:
            return PartialJobs(jobs.values())
        if total and seen < total:
            return PartialJobs(jobs.values())
        return list(jobs.values())


register(SeeMeHiredAdapter())
