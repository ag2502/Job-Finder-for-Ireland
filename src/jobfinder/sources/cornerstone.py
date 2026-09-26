"""Cornerstone OnDemand (csod.com) career-site adapter.

A Cornerstone careers site is a client-side app over a regional search API. The page
carries everything an anonymous visitor's browser needs to call it, as `csod.context`:
the regional API base (`endpoints.cloud`, e.g. https://uk.api.csod.com/), the site's
culture, and a short-lived token issued to every visitor.

    POST {cloud}rec-job-search/external/jobs      Authorization: Bearer {token}
         {"careerSiteId": 5, "pageNumber": 1, "pageSize": 25, ...}
         -> data.totalCount, data.requisitions[]

The slug is ``tenant|siteId``: Three Ireland is ``three-ireland|5``. A requisition
carries its full advert and every location it is open in.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
MAX_PAGES = 40
COMPLETENESS = 0.95


def split_slug(slug: str) -> tuple[str, str]:
    tenant, _, site = slug.partition("|")
    if not tenant:
        raise ValueError(f"Cornerstone slug must be 'tenant|siteId', got {slug!r}")
    return tenant, site or "1"


def page_context(page: str) -> dict:
    """The `csod.context` object a career-site page embeds."""
    start = page.find("csod.context=")
    if start < 0:
        raise ValueError("no csod.context on the career-site page")
    context, _ = json.JSONDecoder().raw_decode(page[start + len("csod.context="):])
    if not context.get("token") or not (context.get("endpoints") or {}).get("cloud"):
        raise ValueError("csod.context carries no token or API endpoint")
    return context


def _place(location: dict) -> str | None:
    parts = [location.get(key) for key in ("city", "state", "country")]
    return ", ".join(part for part in parts if part) or None


class CornerstoneAdapter(BaseAdapter):
    name = "cornerstone"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, site = split_slug(slug)
        home = f"https://{tenant}.csod.com/ux/ats/careersite/{site}/home?c={tenant}"
        response = client.get(home)
        response.raise_for_status()
        context = page_context(response.text)
        culture = context.get("cultureName") or "en-US"
        endpoint = context["endpoints"]["cloud"].rstrip("/") + "/rec-job-search/external/jobs"

        found: dict[str, dict] = {}
        total: int | None = None
        for page_number in range(1, MAX_PAGES + 1):
            result = client.post(
                endpoint,
                json={
                    "careerSiteId": int(site), "careerSitePageId": int(site),
                    "pageNumber": page_number, "pageSize": PAGE_SIZE,
                    "cultureId": int(context.get("cultureID") or 1), "cultureName": culture,
                    "searchText": "", "states": [], "countryCodes": [], "cities": [],
                    "placeID": "", "radius": None, "postingsWithinDays": None,
                    "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                    "customFieldRadios": [],
                },
                headers={"Authorization": f"Bearer {context['token']}"},
            )
            result.raise_for_status()
            data = result.json().get("data") or {}
            if total is None:
                total = int(data.get("totalCount") or 0)
            batch = data.get("requisitions") or []
            for requisition in batch:
                if requisition.get("requisitionId") is not None:
                    found.setdefault(str(requisition["requisitionId"]), requisition)
            if not batch or len(found) >= total:
                break
            self.polite_pause()
        else:
            return PartialJobs(self._build(found, tenant, site, culture))

        if total and len(found) < total * COMPLETENESS:
            raise ValueError(f"Cornerstone read {len(found)} of {total} roles from {tenant}")
        return self._build(found, tenant, site, culture)

    @staticmethod
    def _build(found: dict[str, dict], tenant: str, site: str, culture: str) -> list[RawJob]:
        day_first = not culture.lower().endswith("us")
        jobs = []
        for job_id, requisition in found.items():
            places = [p for p in (_place(loc) for loc in requisition.get("locations") or []) if p]
            jobs.append(RawJob(
                source_job_id=job_id,
                title=(requisition.get("displayJobTitle") or "").strip(),
                url=f"https://{tenant}.csod.com/ux/ats/careersite/{site}/home/requisition/{job_id}?c={tenant}",
                location_raw=places[0] if places else None,
                extra_locations=places[1:],
                description=(requisition.get("externalDescription") or "").strip() or None,
                posted_at=_parse_date(requisition.get("postingEffectiveDate"), day_first),
            ))
        return jobs


def _parse_date(value: str | None, day_first: bool) -> datetime | None:
    if not value or value == "-":
        return None
    try:
        return date_parser.parse(value, dayfirst=day_first)
    except (ValueError, TypeError, OverflowError):
        return None


register(CornerstoneAdapter())
