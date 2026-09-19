"""Occupop (Cezanne Recruitment) careers-page adapter.

    POST https://gateway.server.occupop.com/graphql   (query LiveJobs, companyKey={slug})

Occupop is a Dublin-built ATS widely used by Irish SMEs. Its careers pages
(`{slug}.occupop-careers.com`) are rendered client-side from a public GraphQL gateway, so
the page itself holds nothing to read; the same query the page makes returns every live
job with its description, city, country and the hiring company's name.

The company name matters: `bulk_detect` checks it against the employer the board is
being filed under, because a careers page can link to a sister brand's board.
"""

from __future__ import annotations

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

GATEWAY = "https://gateway.server.occupop.com/graphql"

LIVE_JOBS = """
query LiveJobs($companyKey: String!) {
  careersPage {
    liveJobs(companyKey: $companyKey) {
      uuid
      title
      description
      publishedAt
      companyName
      location { city country }
      subsectors { name }
    }
  }
}
"""


def live_jobs(slug: str, client: httpx.Client) -> list[dict]:
    response = client.post(
        GATEWAY,
        json={"operationName": "LiveJobs", "variables": {"companyKey": slug}, "query": LIVE_JOBS},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ValueError(f"Occupop rejected {slug!r}: {payload['errors'][0].get('message')}")
    return ((payload.get("data") or {}).get("careersPage") or {}).get("liveJobs") or []


def board_owner(jobs: list[dict]) -> str | None:
    return jobs[0].get("companyName") if jobs else None


def _location(item: dict) -> str | None:
    location = item.get("location") or {}
    parts: list[str] = []
    for part in (location.get("city"), location.get("country")):
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts) or None


def _date(value):
    try:
        return date_parser.parse(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


class OccupopAdapter(BaseAdapter):
    name = "occupop"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: list[RawJob] = []
        for item in live_jobs(slug, client):
            uuid = item.get("uuid")
            if not uuid:
                continue
            subsectors = item.get("subsectors") or []
            jobs.append(
                RawJob(
                    source_job_id=uuid,
                    title=item.get("title") or "",
                    url=f"https://{slug}.occupop-careers.com/jobs/{uuid}/apply",
                    location_raw=_location(item),
                    description=item.get("description"),
                    posted_at=_date(item.get("publishedAt")),
                    department=subsectors[0].get("name") if subsectors else None,
                )
            )
        return jobs


register(OccupopAdapter())
