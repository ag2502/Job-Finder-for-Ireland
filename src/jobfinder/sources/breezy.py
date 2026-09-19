"""Breezy HR job board adapter.

    https://{slug}.breezy.hr/json

One request returns every open position with structured locations, but no advert text.
The description lives on each position's own page — in `JobPosting` markup where the
customer's theme emits it, otherwise in the page's `.description` block — so it is read
from there — capped, because a description is worth having but not worth an
unbounded crawl, and a posting without one is still a posting.

The feed names the board's owner (`company.name`), which registration checks against the
company it is being filed under.
"""

from __future__ import annotations

import logging

import httpx
from dateutil import parser as date_parser

from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import _is_job_posting, _text, iter_ld_objects

logger = logging.getLogger(__name__)

MAX_DESCRIPTIONS = 60


def board_owner(payload) -> str | None:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return (payload[0].get("company") or {}).get("name")
    return None


def _location_name(location: dict) -> str | None:
    if location.get("is_remote") and not location.get("city"):
        return "Remote"
    parts = [
        location.get("city"),
        (location.get("state") or {}).get("name"),
        (location.get("country") or {}).get("name"),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or location.get("name")


class BreezyAdapter(BaseAdapter):
    name = "breezy"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"https://{slug}.breezy.hr/json")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"unexpected Breezy payload for {slug!r}")

        jobs: list[RawJob] = []
        for index, item in enumerate(payload):
            job_id = item.get("id")
            if not job_id:
                continue
            names = [
                name
                for name in (_location_name(loc) for loc in item.get("locations") or [])
                if name
            ]
            primary = _location_name(item.get("location") or {}) or (names[0] if names else None)
            url = item.get("url") or f"https://{slug}.breezy.hr/"
            published = item.get("published_date")
            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("name") or "",
                    url=url,
                    location_raw=primary,
                    description=self._description(url, client) if index < MAX_DESCRIPTIONS else None,
                    posted_at=date_parser.parse(published) if published else None,
                    department=item.get("department"),
                    extra_locations=[name for name in names if name != primary],
                )
            )
        return jobs

    def _description(self, url: str, client: httpx.Client) -> str | None:
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("breezy: no description from %s: %r", url, exc)
            return None
        finally:
            self.polite_pause()
        for obj in iter_ld_objects(response.text):
            if _is_job_posting(obj):
                return _text(obj.get("description"))
        block = HTMLParser(response.text).css_first(".description")
        return block.html if block is not None else None


register(BreezyAdapter())
