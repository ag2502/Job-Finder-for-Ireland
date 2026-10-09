"""Personio job board adapter.

    https://{slug}.jobs.personio.de/xml

Personio is the only Tier 1 board here that publishes XML rather than JSON — the feed
element is still called ``<workzag-jobs>`` after the company's pre-2015 name. It is
unauthenticated and carries full descriptions inline, so one request covers a board.

Tenants sit on either `.jobs.personio.de` or `.jobs.personio.com` with no way to tell
which from the slug, so the adapter tries `.de` and falls back to `.com`. A slug may
also pin the host explicitly as ``acme:com`` to skip the wasted first request, which is
what detection records once it has seen which host answered.

The advert is split across repeated ``<jobDescription>`` elements — "Your responsibilities",
"Your profile", and so on — which are concatenated in feed order. Reading only the first
would drop the requirements, which is where most of the matchable skill vocabulary is.

A missing tenant returns a 404 rather than an empty feed, so the base adapter's FAILED
result correctly stops the reconciler from closing anything.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

HOSTS = ("de", "com")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def split_slug(slug: str) -> tuple[str, tuple[str, ...]]:
    """Split ``acme`` or ``acme:com`` into (tenant, hosts to try, in order)."""
    tenant, _, host = slug.partition(":")
    if host in HOSTS:
        return tenant, (host,)
    return tenant, HOSTS


def _descriptions(position: ET.Element) -> str | None:
    """Concatenate every ``<jobDescription>`` block in feed order."""
    chunks: list[str] = []
    for block in position.iterfind("jobDescriptions/jobDescription"):
        heading = _text(block.find("name"))
        body = _text(block.find("value"))
        if not body:
            continue
        chunks.append(f"{heading}\n{body}" if heading else body)
    return "\n\n".join(chunks) or None


def _offices(position: ET.Element) -> tuple[str | None, list[str]]:
    """Return (primary office, additional offices).

    Most feeds carry a single ``<office>``. Multi-site tenants add an ``<offices>``
    wrapper, which is a genuine per-posting list rather than a company-wide one.
    """
    primary = _text(position.find("office"))

    extras: list[str] = []
    for entry in position.iterfind("offices/office"):
        name = _text(entry)
        if name and name != primary:
            extras.append(name)

    if primary is None and extras:
        primary, extras = extras[0], extras[1:]
    return primary, extras


class PersonioAdapter(BaseAdapter):
    name = "personio"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, hosts = split_slug(slug)
        try:
            text, host = self._fetch_feed(tenant, hosts, client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # A tenant can switch the XML feed off while its careers page stays up:
            # Xtremepush's /xml is a 404, but its board's own search.json still lists
            # every position. Only a board missing on every host is a missing tenant.
            listed = self._fetch_search(tenant, hosts, client)
            if listed is None:
                raise
            return listed

        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError(f"malformed Personio feed for {slug!r}: {exc}") from exc

        jobs: list[RawJob] = []
        for position in root.iterfind("position"):
            job_id = _text(position.find("id"))
            if not job_id:
                continue

            primary, extras = _offices(position)

            jobs.append(
                RawJob(
                    source_job_id=job_id,
                    title=_text(position.find("name")) or "",
                    url=f"https://{tenant}.jobs.personio.{host}/job/{job_id}",
                    location_raw=primary,
                    description=_descriptions(position),
                    posted_at=_parse_date(_text(position.find("createdAt"))),
                    department=_text(position.find("department"))
                    or _text(position.find("recruitingCategory")),
                    extra_locations=extras,
                    employment_type=_text(position.find("schedule")),
                )
            )
        return jobs

    def _fetch_search(
        self, tenant: str, hosts: tuple[str, ...], client: httpx.Client
    ) -> list[RawJob] | None:
        """The positions from the careers page's JSON search, or None if it has none."""
        for host in hosts:
            response = client.get(f"https://{tenant}.jobs.personio.{host}/search.json")
            if response.status_code == 404:
                continue
            response.raise_for_status()
            jobs = []
            for item in response.json():
                if not isinstance(item, dict) or item.get("id") is None:
                    continue
                offices = [o for o in item.get("offices") or [] if o]
                primary = item.get("office") or (offices[0] if offices else None)
                jobs.append(RawJob(
                    source_job_id=str(item["id"]),
                    title=item.get("name") or "",
                    url=f"https://{tenant}.jobs.personio.{host}/job/{item['id']}",
                    location_raw=primary,
                    extra_locations=[o for o in offices if o != primary],
                    description=item.get("description") or None,
                    department=item.get("department") or None,
                    employment_type=item.get("schedule") or None,
                ))
            return jobs
        return None

    def _fetch_feed(
        self, tenant: str, hosts: tuple[str, ...], client: httpx.Client
    ) -> tuple[str, str]:
        """Fetch the feed from the first host that serves it.

        Only a 404 is treated as "wrong host and worth retrying elsewhere". Any other
        error is raised, so a 500 or a timeout stays a failure rather than being
        misreported as a missing tenant.
        """
        last_error: httpx.HTTPStatusError | None = None

        for host in hosts:
            response = client.get(f"https://{tenant}.jobs.personio.{host}/xml")
            if response.status_code == 404:
                last_error = httpx.HTTPStatusError(
                    "404", request=response.request, response=response
                )
                logger.debug("personio: no tenant %s on .%s", tenant, host)
                continue
            response.raise_for_status()
            return response.text, host

        assert last_error is not None  # hosts is never empty
        raise last_error


register(PersonioAdapter())
