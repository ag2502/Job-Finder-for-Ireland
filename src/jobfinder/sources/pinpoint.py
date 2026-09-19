"""Pinpoint job board adapter.

    https://{slug}.pinpointhq.com/postings.json

One request returns every open posting. The advert is split across several HTML
sections (description, responsibilities, skills, benefits), each with its own heading,
and they are joined in reading order so the matcher sees the whole advert.

Locations carry a city and province but no country, and a remote role says so only in
`workplace_type`, so that is folded into the location string for the normalizer to read.
The feed does not name the board's owner; registration relies on the slug resembling
the company.
"""

from __future__ import annotations

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register

SECTIONS = (
    ("description", None),
    ("key_responsibilities", "key_responsibilities_header"),
    ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
    ("benefits", "benefits_header"),
)


def _location(item: dict) -> str | None:
    location = item.get("location") or {}
    parts: list[str] = []
    for part in (location.get("city"), location.get("province")):
        if part and part not in parts:
            parts.append(part)
    if item.get("workplace_type") == "remote":
        parts.append("Remote")
    return ", ".join(parts) or location.get("name")


def _description(item: dict) -> str | None:
    blocks: list[str] = []
    for field, header in SECTIONS:
        body = item.get(field)
        if not body:
            continue
        title = item.get(header) if header else None
        blocks.append(f"<h3>{title}</h3>{body}" if title else body)
    return "\n".join(blocks) or None


class PinpointAdapter(BaseAdapter):
    name = "pinpoint"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"https://{slug}.pinpointhq.com/postings.json")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError(f"unexpected Pinpoint payload for {slug!r}")

        jobs: list[RawJob] = []
        for item in payload["data"]:
            job_id = item.get("id")
            if not job_id:
                continue
            department = ((item.get("job") or {}).get("department") or {}).get("name")
            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("title") or "",
                    url=item.get("url") or f"https://{slug}.pinpointhq.com{item.get('path') or ''}",
                    location_raw=_location(item),
                    description=_description(item),
                    department=department,
                )
            )
        return jobs


register(PinpointAdapter())
