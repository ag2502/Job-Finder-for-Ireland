"""Phenom People career-site adapter.

Phenom renders careers sites for large employers - Kerry, BAM, Zimmer Biomet - on the
employer's own domain, and every one of them searches through the same endpoint:

    POST https://{site}/widgets
         {"ddoKey": "refineSearch", "selected_fields": {"country": ["Ireland"]},
          "from": 0, "size": 50, "lang": "en_gb", "country": "gb", ...}

The site's locale, country and reference number are read from the home page's
`phApp.ddo` settings, since the widget answers only in the site's own locale.

Many Phenom sites are only a front for a Workday or Eightfold board, and those are
better read from the board itself (Cisco, HPE, Adobe are). This adapter is for the
ones whose back end cannot be read: Kerry's apply links go to a SuccessFactors
instance with no public search.

The country filter uses the site's own facet value, which is not always the name:
BAM's is "IRL". A site whose facets list no Irish value has no Irish roles, which is an
answer; a response with no facets at all is not, and fails rather than closing roles.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_PAGES = 40
DEFAULT_COUNTRY = "Ireland"
# Facet values sites use for Ireland besides the name itself.
COUNTRY_ALIASES = {"ireland": {"ireland", "irl", "ie", "republic of ireland", "éire", "eire"}}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


def _setting(page: str, key: str) -> str | None:
    match = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(key), page)
    return match.group(1) if match else None


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


class PhenomAdapter(BaseAdapter):
    name = "phenom"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        target, _, country_name = slug.partition("|")
        country_name = country_name or DEFAULT_COUNTRY
        start = target if "//" in target else f"https://{target}"

        home = client.get(start, headers=BROWSER_HEADERS)
        home.raise_for_status()
        site = self._site(home.text, str(home.url))

        facets = self._search(client, site, {}, 0, 1)
        aggregations = (facets.get("data") or {}).get("aggregations")
        if not isinstance(aggregations, list):
            raise ValueError(f"phenom site {site['origin']} returned no facets")
        wanted = COUNTRY_ALIASES.get(country_name.lower(), {country_name.lower()})
        values = [
            value
            for aggregation in aggregations
            if aggregation.get("field") == "country"
            for value in (aggregation.get("value") or {})
            if value.strip().lower() in wanted
        ]
        if not values:
            return []

        found: dict[str, dict] = {}
        total: int | None = None
        for page in range(MAX_PAGES):
            result = self._search(client, site, {"country": values}, page * PAGE_SIZE, PAGE_SIZE)
            if total is None:
                total = int(result.get("totalHits") or 0)
            batch = (result.get("data") or {}).get("jobs") or []
            for job in batch:
                job_id = str(job.get("jobId") or job.get("reqId") or "").strip()
                if job_id:
                    found.setdefault(job_id, job)
            if not batch or len(found) >= total:
                break
            self.polite_pause()
        else:
            return PartialJobs(self._build(client, site, job) for job in found.values())

        return [self._build(client, site, job) for job in found.values()]

    @staticmethod
    def _site(page: str, url: str) -> dict:
        locale = _setting(page, "locale")
        country = _setting(page, "country")
        if not (locale and country):
            raise ValueError(f"no Phenom site settings found at {url}")
        parts = urlsplit(url)
        return {
            "origin": f"{parts.scheme}://{parts.netloc}",
            "lang": locale,
            "country": country,
            "refNum": _setting(page, "refNum") or "",
            "siteType": _setting(page, "siteType") or "external",
            "prefix": f"/{country}/{locale.split('_')[0]}",
        }

    def _search(self, client: httpx.Client, site: dict, fields: dict, start: int, size: int) -> dict:
        response = client.post(
            f"{site['origin']}/widgets",
            json={
                "lang": site["lang"], "deviceType": "desktop", "country": site["country"],
                "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "",
                "subsearch": "", "from": start, "jobs": True, "counts": True,
                "all_fields": ["country"], "size": size, "clearAll": False,
                "jdsource": "facets", "isSliderEnable": False, "pageId": "page1",
                "siteType": site["siteType"], "keywords": "", "global": True,
                "selected_fields": fields, "locationData": {},
            },
            headers=BROWSER_HEADERS,
        )
        response.raise_for_status()
        result = response.json().get("refineSearch")
        if not isinstance(result, dict):
            raise ValueError(f"unexpected Phenom search payload from {site['origin']}")
        return result

    def _description(self, client: httpx.Client, site: dict, job_id: str) -> str | None:
        """The full advert; the search result carries only a teaser. Best effort."""
        try:
            response = client.post(
                f"{site['origin']}/widgets",
                json={
                    "lang": site["lang"], "deviceType": "desktop", "country": site["country"],
                    "pageName": "job", "ddoKey": "jobDetail", "jobId": job_id,
                    "siteType": site["siteType"], "refNum": site["refNum"],
                },
                headers=BROWSER_HEADERS,
            )
            response.raise_for_status()
            job = ((response.json().get("jobDetail") or {}).get("data") or {}).get("job") or {}
            return job.get("description") or None
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("phenom detail %s failed: %s", job_id, exc)
            return None
        finally:
            self.polite_pause()

    def _build(self, client: httpx.Client, site: dict, job: dict) -> RawJob:
        job_id = str(job.get("jobId") or job.get("reqId"))
        primary = job.get("location") or ", ".join(
            part for part in (job.get("city"), job.get("country")) if part
        ) or None
        extras = [loc for loc in job.get("multi_location") or [] if loc and loc != primary]
        return RawJob(
            source_job_id=job_id,
            title=job.get("title") or "",
            url=f"{site['origin']}{site['prefix']}/job/{job_id}",
            location_raw=primary,
            extra_locations=extras,
            description=self._description(client, site, job_id) or job.get("descriptionTeaser"),
            posted_at=_parse_date(job.get("postedDate")),
            department=job.get("category"),
        )


register(PhenomAdapter())
