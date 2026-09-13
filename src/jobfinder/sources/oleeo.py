"""Oleeo (TAL.net) vacancy board adapter.

    https://{host}/vx/mobile-0/appcentre-1/candidate/jobboard/vacancy/{board}/feed

Oleeo, still served from `*.tal.net`, runs the boards of large Irish and UK employers —
Dunnes Stores here. The board page paginates in hundreds, but every board also publishes
an Atom feed of all open vacancies in one document, and the feed is the better source:
each entry carries a structured block of `Key:Value` lines including the county, closing
date and employment type, where the page shows only a title.

The slug is ``host|board``: for Dunnes, ``dunnes.tal.net|3``.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

ATOM = "{http://www.w3.org/2005/Atom}"
FIELD = re.compile(r"([A-Za-z][A-Za-z ]{1,40}?)\s*:\s*([^\n]+)")
OPP_ID = re.compile(r"/opp/(\d+)-")
BOARD_URL = re.compile(r"https?://([a-z0-9-]+\.tal\.net)/[^\"'\s<>]*?/jobboard/vacancy/(\d+)", re.I)


def slug_from_url(url: str) -> str | None:
    """``host|board`` from any Oleeo job-board URL."""
    match = BOARD_URL.search(url)
    return f"{match.group(1).lower()}|{match.group(2)}" if match else None


def feed_url(slug: str) -> str:
    host, _, board = slug.partition("|")
    if not host or not board:
        raise ValueError(f"Oleeo slug must be 'host|board', got {slug!r}")
    return f"https://{host}/vx/mobile-0/appcentre-1/candidate/jobboard/vacancy/{board}/feed"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _fields(content: ET.Element | None) -> dict[str, str]:
    """The `Key:Value` lines of an entry's xhtml content block."""
    if content is None:
        return {}
    lines: list[str] = []
    for node in content.iter():
        for piece in (node.text, node.tail):
            if piece and piece.strip():
                lines.append(piece.strip())
    fields: dict[str, str] = {}
    for line in lines:
        match = FIELD.match(line)
        if match:
            fields.setdefault(match.group(1).strip().lower(), html.unescape(match.group(2).strip()))
    return fields


class OleeoAdapter(BaseAdapter):
    name = "oleeo"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(feed_url(slug))
        response.raise_for_status()
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ValueError(f"malformed Oleeo feed for {slug!r}: {exc}") from exc
        if root.tag != f"{ATOM}feed":
            raise ValueError(f"Oleeo feed for {slug!r} is not an Atom feed")

        jobs: dict[str, RawJob] = {}
        for entry in root.iter(f"{ATOM}entry"):
            title = (entry.findtext(f"{ATOM}title") or "").strip()
            link = entry.find(f"{ATOM}link")
            url = (link.get("href") if link is not None else None) or entry.findtext(f"{ATOM}id") or ""
            url = url.split("?", 1)[0]
            fields = _fields(entry.find(f"{ATOM}content"))

            match = OPP_ID.search(url)
            job_id = fields.get("id") or (match.group(1) if match else None)
            if not title or not job_id or not url:
                continue

            location = fields.get("location") or fields.get("county")
            jobs.setdefault(
                job_id,
                RawJob(
                    source_job_id=job_id,
                    title=title,
                    url=url,
                    location_raw=location,
                    posted_at=_parse_date(entry.findtext(f"{ATOM}published")),
                    department=fields.get("role type") or fields.get("department"),
                    description="\n".join(f"{k.title()}: {v}" for k, v in fields.items()) or None,
                ),
            )
        return list(jobs.values())


register(OleeoAdapter())
