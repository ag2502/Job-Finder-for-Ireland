"""WordPress vacancy post types.

Many Irish employers' careers pages are WordPress, with vacancies stored as their own
post type (`vacancy`, `job`, WP Job Manager's `job-listings`). WordPress publishes
every public post type through its REST API, paged, with the total in a header:

    GET https://{site}/wp-json/wp/v2/{rest_base}?per_page=100&page=1
        X-WP-Total: 55   X-WP-TotalPages: 1

That is read here instead of scraping the rendered page, which on these sites is
usually built by script. The slug is ``host|rest_base``, e.g.
``musgravegroup.com|vacancy``.

Location is the awkward part: there is no standard field. WP Job Manager keeps it in
`meta._job_location`; custom types usually state it in the advert ("Location: Centra
Carrickfergus"). Both are tried, and a site with a fixed location may name it as a
third slug part (``host|rest_base|Dublin, Ireland``).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 30
_TAGS = re.compile(r"<[^>]+>")
_LOCATION = re.compile(r"\bLocation\s*[:\-–]?\s*(?:&nbsp;|\s)*([^\n<]{2,90})", re.I)


def split_slug(slug: str) -> tuple[str, str, str | None]:
    host, rest_base, place = (slug.split("|") + ["", ""])[:3]
    if not (host and rest_base):
        raise ValueError(f"WordPress slug must be 'host|rest_base', got {slug!r}")
    return host, rest_base, place or None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", fragment or ""))).strip()


def _location(item: dict) -> str | None:
    for fields in (item.get("meta"), item.get("acf")):
        if not isinstance(fields, dict):
            continue
        for key in ("_job_location", "job_location", "location"):
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    content = html.unescape(_TAGS.sub("\n", (item.get("content") or {}).get("rendered") or ""))
    match = _LOCATION.search(content)
    if not match:
        return None
    return match.group(1).replace("\xa0", " ").strip(" .,:") or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class WordPressAdapter(BaseAdapter):
    name = "wordpress"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, rest_base, place = split_slug(slug)
        endpoint = f"https://{host}/wp-json/wp/v2/{rest_base}"

        items: dict[str, dict] = {}
        total: int | None = None
        for page in range(1, MAX_PAGES + 1):
            response = client.get(endpoint, params={"per_page": PAGE_SIZE, "page": page})
            if page > 1 and response.status_code == 400:
                break  # WordPress answers a page past the end with 400
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError(f"unexpected WordPress payload from {endpoint}")
            if total is None:
                total = int(response.headers.get("X-WP-Total") or len(batch))
                pages = int(response.headers.get("X-WP-TotalPages") or 1)
            for item in batch:
                if item.get("id") is not None and item.get("status", "publish") == "publish":
                    items.setdefault(str(item["id"]), item)
            if page >= pages or not batch:
                break
            self.polite_pause()
        else:
            return PartialJobs(self._build(items, place))

        if total and len(items) < total:
            raise ValueError(f"WordPress listed {len(items)} of {total} posts at {endpoint}")
        return self._build(items, place)

    @staticmethod
    def _build(items: dict[str, dict], place: str | None) -> list[RawJob]:
        jobs = []
        for job_id, item in items.items():
            title = _text((item.get("title") or {}).get("rendered"))
            if not title:
                continue
            jobs.append(RawJob(
                source_job_id=job_id,
                title=title,
                url=item.get("link") or "",
                location_raw=_location(item) or place,
                description=(item.get("content") or {}).get("rendered") or None,
                posted_at=_parse_date(item.get("date_gmt") or item.get("date")),
            ))
        return jobs


register(WordPressAdapter())
