"""Adzuna aggregator adapter.

    https://api.adzuna.com/v1/api/jobs/ie/search/{page}
        ?app_id=...&app_key=...&results_per_page=50&where=dublin

Adzuna publishes a documented API with a free tier and genuine Irish coverage, which
makes it the most useful of the aggregators here. The `ie` country segment scopes the
whole index to Ireland, so unlike the ATS adapters there is no global result set to
filter down.

Each posting names its employer as free text (`company.display_name`). That text is
resolved to a `Company` by the reconciler, so an aggregated posting lands against the
same company row as that employer's own board — which is what allows `dedup_key` to
recognise the two as one job and prefer the direct source.

The slug is the search term, so one source is one query. That is deliberate: `where=dublin`
and `what=software engineer` are different sources with different result-set sizes, and
giving them separate rows lets the volume-drop breaker reason about each independently.
A slug of `dublin` means "everything in Dublin".

Credentials come from `JOBFINDER_ADZUNA_APP_ID` and `JOBFINDER_ADZUNA_APP_KEY`. Without
them the adapter fails cleanly rather than raising, so an unconfigured aggregator is
simply an unreachable source and closes nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from jobfinder.core.config import settings
from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

API_ROOT = "https://api.adzuna.com/v1/api/jobs"
COUNTRY = "ie"

PAGE_SIZE = 50
MAX_PAGES = 20


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def format_location(location: dict | None) -> str | None:
    """Render Adzuna's `location` object.

    `display_name` is already a human-readable string ("Dublin, County Dublin"), and the
    `area` array is the same place from country down to locality. The display name is
    preferred; the area array is reversed as the fallback so the most specific part
    leads, which is the order the location normalizer expects.
    """
    if not isinstance(location, dict):
        return None

    display = location.get("display_name")
    if isinstance(display, str) and display.strip():
        return display.strip()

    area = location.get("area")
    if isinstance(area, list) and area:
        parts = [str(part) for part in reversed(area) if part]
        return ", ".join(parts) or None
    return None


class AdzunaAdapter(BaseAdapter):
    name = "adzuna"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        app_id = settings.adzuna_app_id
        app_key = settings.adzuna_app_key
        if not app_id or not app_key:
            raise ValueError(
                "Adzuna credentials not configured "
                "(set JOBFINDER_ADZUNA_APP_ID and JOBFINDER_ADZUNA_APP_KEY)"
            )

        where, what = _split_slug(slug)

        jobs: list[RawJob] = []
        seen: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": PAGE_SIZE,
                "content-type": "application/json",
            }
            if where:
                params["where"] = where
            if what:
                params["what"] = what

            response = client.get(f"{API_ROOT}/{COUNTRY}/search/{page}", params=params)
            response.raise_for_status()
            payload = response.json()

            results = payload.get("results") or []
            if not results:
                break

            for item in results:
                job = _to_raw_job(item)
                if job is None or job.source_job_id in seen:
                    continue
                seen.add(job.source_job_id)
                jobs.append(job)

            if len(results) < PAGE_SIZE:
                break
            self.polite_pause()

        return jobs


def _split_slug(slug: str) -> tuple[str, str]:
    """`dublin` -> (dublin, ''); `dublin:data engineer` -> (dublin, 'data engineer')."""
    where, _, what = slug.partition(":")
    return where.strip(), what.strip()


def _to_raw_job(item: dict) -> RawJob | None:
    job_id = item.get("id")
    title = item.get("title")
    if not job_id or not title:
        return None

    company = item.get("company") or {}

    return RawJob(
        source_job_id=str(job_id),
        title=str(title),
        url=item.get("redirect_url") or "",
        location_raw=format_location(item.get("location")),
        description=item.get("description"),
        posted_at=_parse_date(item.get("created")),
        department=(item.get("category") or {}).get("label"),
        # Resolved to a Company row by the reconciler; this is what lets an aggregated
        # posting share a dedup group with the employer's own listing of the same role.
        company_name=company.get("display_name"),
    )


register(AdzunaAdapter())
