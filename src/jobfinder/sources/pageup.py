"""PageUp career sites, at careers.pageuppeople.com/{tenant}/cw/en/.

Every PageUp tenant publishes its whole job list as one RSS feed, `/{tenant}/cw/en/rss`,
with a structured location ("Republic of Ireland|Tallaght"), the work type ("Part
time"), the advert and the apply link. Halfords' feed carries about 400 roles across
the UK and Ireland, and only those placed in the Republic are kept.

The slug is the tenant number, `806` for Halfords.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
from dateutil import parser as date_parser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, RawJob, register

FEED = "https://careers.pageuppeople.com/{tenant}/cw/en/rss"
NS = "{http://pageuppeople.com/}"


def _place(raw: str | None) -> str | None:
    """'Republic of Ireland|Tallaght' -> 'Tallaght, Ireland'."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if parts and parts[0].lower() == "republic of ireland":
        return ", ".join(parts[1:] + ["Ireland"])
    return ", ".join(reversed(parts))


def parse_feed(xml: str) -> list[RawJob]:
    root = ET.fromstring(xml)
    jobs: list[RawJob] = []
    for item in root.iter("item"):
        ref = item.findtext(f"{NS}refNo") or item.findtext("guid")
        title = (item.findtext("title") or "").strip()
        if not ref or not title:
            continue
        location = _place(item.findtext(f"{NS}location"))
        if not normalize_location(location).is_ireland:
            continue
        published = item.findtext("pubDate")
        try:
            posted_at = date_parser.parse(published) if published else None
        except (ValueError, OverflowError):
            posted_at = None
        jobs.append(RawJob(
            source_job_id=ref.rstrip("/").rsplit("/", 1)[-1],
            title=title,
            url=item.findtext("link") or item.findtext("guid") or "",
            location_raw=location,
            description=item.findtext(f"{NS}description") or item.findtext("description"),
            posted_at=posted_at,
            department=item.findtext(f"{NS}businessLayer1") or None,
            employment_type=item.findtext(f"{NS}workType") or None,
        ))
    return jobs


class PageUpAdapter(BaseAdapter):
    name = "pageup"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(FEED.format(tenant=slug.strip()))
        response.raise_for_status()
        try:
            return parse_feed(response.text)
        except ET.ParseError as exc:
            raise ValueError(f"malformed PageUp feed for tenant {slug!r}: {exc}") from exc


register(PageUpAdapter())
