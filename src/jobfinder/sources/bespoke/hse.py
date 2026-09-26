"""Health Service Executive adapter.

The HSE is the largest employer in the State, and it lists its vacancies on its own
site, server-rendered ten to a page:

    https://about.hse.ie/jobs/job-search/?page=1

Each card names the role, its category, its county and the date it was posted. Pages
are numbered from 1 and one past the end is empty. The page states a total, which moves
while it is read as adverts go up and come down, so the read is held to most of it
rather than all of it.

A "Confined competition" is open only to people already employed in the service, so it
is left out: offering it to the public would send people to apply for a role they cannot
hold. The county is the only location given; "Dublin South" and "Dublin North" are the
HSE's Dublin regions, and "All" means a national competition.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

LIST_URL = "https://about.hse.ie/jobs/job-search/"
ORIGIN = "https://about.hse.ie"
MAX_PAGES = 80
COMPLETENESS = 0.9

_ITEM = re.compile(
    r'<li class="hse-listing__item[^"]*"[^>]*>(.*?)</li>\s*(?=<li class="hse-listing__item|</ol>)',
    re.S,
)
_LINK = re.compile(r'<a\b[^>]*href="(/jobs/job-search/([a-z0-9-]+)/)"[^>]*>(.*?)</a>', re.S)
_TOTAL = re.compile(r"(\d[\d,]*)\s*jobs?\b", re.I)
_TAGS = re.compile(r"<[^>]+>")


def _field(item: str, label: str) -> str | None:
    match = re.search(rf"{label}:\s*([^<]+)", item)
    return html.unescape(match.group(1)).strip() if match else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def parse_page(page: str) -> list[dict]:
    """The vacancy cards on one results page."""
    cards = []
    for item in _ITEM.findall(page):
        link = _LINK.search(item)
        if not link:
            continue
        posted = re.search(r'<time[^>]*dateTime="([^"]+)"', item)
        cards.append({
            "path": link.group(1),
            "id": link.group(2),
            "title": html.unescape(_TAGS.sub("", link.group(3))).strip(),
            "county": _field(item, "County"),
            "category": _field(item, "Category"),
            "type": _field(item, "Advertisement Type"),
            "posted": posted.group(1) if posted else None,
        })
    return cards


def _location(county: str | None) -> str:
    if not county or county.lower() == "all":
        return "Ireland"
    return f"{county}, Ireland"


class HSEAdapter(BaseAdapter):
    name = "hse"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        cards: dict[str, dict] = {}
        total: int | None = None

        for page_number in range(1, MAX_PAGES + 1):
            response = client.get(LIST_URL, params={"page": page_number})
            response.raise_for_status()
            if total is None:
                match = _TOTAL.search(response.text)
                total = int(match.group(1).replace(",", "")) if match else None
            page = parse_page(response.text)
            if not page:
                break
            for card in page:
                cards.setdefault(card["id"], card)
            self.polite_pause()
        else:
            return PartialJobs(self._jobs(cards))

        if not cards:
            raise ValueError("HSE job search listed no vacancies - markup changed")
        if total and len(cards) < total * COMPLETENESS:
            raise ValueError(f"HSE read {len(cards)} of {total} vacancies; refusing an incomplete list")
        return self._jobs(cards)

    @staticmethod
    def _jobs(cards: dict[str, dict]) -> list[RawJob]:
        jobs = []
        for card in cards.values():
            if (card["type"] or "").lower().startswith("confined"):
                continue
            jobs.append(RawJob(
                source_job_id=card["id"],
                title=card["title"],
                url=f"{ORIGIN}{card['path']}",
                location_raw=_location(card["county"]),
                description=f"Category: {card['category']}" if card["category"] else None,
                posted_at=_parse_date(card["posted"]),
                department=card["category"],
            ))
        return jobs


register(HSEAdapter())
