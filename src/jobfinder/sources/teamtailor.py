"""Teamtailor careers adapter.

    https://{host}/jobs.rss

Every Teamtailor career site — whether on `{slug}.teamtailor.com` or a custom domain such
as `careers.phorest.com` — serves an unauthenticated RSS feed of all published jobs, with
structured locations in a `tt:` namespace. The JSON API needs a per-customer key; the
feed does not, and carries the same postings.

The slug is either a bare Teamtailor subdomain (`acme`) or a full careers host
(`careers.acme.ie`), because detection sees both and neither can be derived from the
other.

`tt:locations` is a genuine per-posting list, so extra offices are expanded — a role
listed in both London and Dublin keeps its Dublin location.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

JOB_ID = re.compile(r"/jobs/(\d+)")


def feed_url(slug: str) -> str:
    host = slug if "." in slug else f"{slug}.teamtailor.com"
    return f"https://{host}/jobs.rss"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _locations(item: ET.Element) -> list[str]:
    container = _child(item, "locations")
    if container is None:
        return []
    found: list[str] = []
    for location in container:
        if _local(location.tag) != "location":
            continue
        city = _text(_child(location, "city"))
        country = _text(_child(location, "country"))
        name = _text(_child(location, "name"))
        label = ", ".join(part for part in (city, country) if part) or name
        if label and label not in found:
            found.append(label)
    return found


class TeamtailorAdapter(BaseAdapter):
    name = "teamtailor"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(feed_url(slug))
        response.raise_for_status()

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ValueError(f"malformed Teamtailor feed for {slug!r}: {exc}") from exc

        channel = _child(root, "channel")
        if channel is None:
            raise ValueError(f"no RSS channel in Teamtailor feed for {slug!r}")

        jobs: list[RawJob] = []
        for item in channel:
            if _local(item.tag) != "item":
                continue
            title = _text(_child(item, "title"))
            link = _text(_child(item, "link"))
            if not title or not link:
                continue

            match = JOB_ID.search(link)
            job_id = match.group(1) if match else (_text(_child(item, "guid")) or link)

            locations = _locations(item)
            remote = (_text(_child(item, "remoteStatus")) or "").lower() == "fully"
            if not locations and remote:
                locations = ["Remote"]

            jobs.append(
                RawJob(
                    source_job_id=job_id,
                    title=title,
                    url=link,
                    location_raw=locations[0] if locations else None,
                    extra_locations=locations[1:],
                    description=_text(_child(item, "description")),
                    posted_at=_parse_date(_text(_child(item, "pubDate"))),
                    department=_text(_child(item, "department")),
                )
            )
        return jobs


register(TeamtailorAdapter())
