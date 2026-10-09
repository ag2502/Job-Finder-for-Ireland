"""Occupop (Cezanne Recruitment) careers-page adapter.

    POST https://gateway.server.occupop.com/graphql   (query LiveJobs, companyKey={slug})

Occupop is a Dublin-built ATS widely used by Irish SMEs. Its careers pages
(`{slug}.occupop-careers.com`) are rendered client-side from a public GraphQL gateway, so
the page itself holds nothing to read; the same query the page makes returns every live
job with its description, city, country and the hiring company's name.

The company name matters: `bulk_detect` checks it against the employer the board is
being filed under, because a careers page can link to a sister brand's board.

Many employers never host an Occupop careers page at all: they drop Occupop's vacancy
frame into their own site instead, addressed by an embed token rather than a company
key, and the gateway rejects that token. Those boards are read from the frame's own
HTML, under a slug of `frame:{token}`. It carries each job's title, place, sector and
contract type but no advert body, since the apply page behind it is a second
single-page app.
"""

from __future__ import annotations

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, RawJob, register

GATEWAY = "https://gateway.server.occupop.com/graphql"
FRAME = "https://api.occupop.com/api/jobs-frame/{token}"
FRAME_PREFIX = "frame:"

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


def frame_jobs(token: str, client: httpx.Client) -> list[RawJob]:
    """The postings in an embedded vacancy frame."""
    response = client.get(
        FRAME.format(token=token),
        params={"visibility": "external", "fields": "title,type,location,sector"},
    )
    response.raise_for_status()

    jobs: list[RawJob] = []
    for row in HTMLParser(response.text).css("tr"):
        link = row.css_first("h4.title a")
        if link is None:
            continue
        url = link.attributes.get("href") or ""
        title = link.text(strip=True)
        if not url or not title:
            continue

        def _small(name: str) -> str | None:
            node = row.css_first(f"small.{name}")
            return node.text(strip=True) or None if node is not None else None

        jobs.append(
            RawJob(
                # The apply URL's slug ends in the job's own identifier and is stable.
                source_job_id=url.rstrip("/").rsplit("/", 1)[-1],
                title=title,
                url=url,
                location_raw=_small("location"),
                department=_small("category"),
                employment_type=_small("type"),
            )
        )
    if not jobs and "jobs-frame" not in response.text and "no-results" not in response.text:
        raise ValueError(f"Occupop frame {token!r} returned no job table")
    return jobs


class OccupopAdapter(BaseAdapter):
    name = "occupop"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        if slug.startswith(FRAME_PREFIX):
            return frame_jobs(slug[len(FRAME_PREFIX):], client)

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
