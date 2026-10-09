"""JobAlert.ie, an Irish-owned job board strong on hotels, shops, care and trades.

    https://www.jobalert.ie/jobs?page={n}

About 2,000 open adverts, a sixth of them part-time, and most from employers too small
to run an ATS of their own: a SuperValu franchisee, a Leitrim garage, a Wicklow country
house hotel. Each results page is server-rendered with its ten jobs in the Next.js page
data, every one carrying the employer, the address, the full advert and its job types
("Full-time", "Part-time"), so no per-job request is needed. The site has no robots.txt,
and its terms say nothing about automated access.
"""

from __future__ import annotations

import json
import re

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

ORIGIN = "https://www.jobalert.ie"
PAGE = ORIGIN + "/jobs?page={page}"
# The board pages ten at a time; 2,000 adverts is 200 pages. Past this ceiling the read
# is a sample, and is reported as one.
MAX_PAGES = 400

NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def page_jobs(html: str) -> tuple[list[dict], int]:
    """The job records on one results page, and the board's total."""
    match = NEXT_DATA.search(html)
    if not match:
        raise ValueError("JobAlert page carries no page data")
    state = json.loads(match.group(1))["props"]["pageProps"]["initialReduxState"]
    jobs = state["entities"]["jobs"]
    return list(jobs.get("data") or []), int(jobs.get("count") or 0)


def to_raw_job(item: dict) -> RawJob | None:
    if item.get("status") not in (None, "OPEN") or item.get("isOpen") is False:
        return None
    slug, title = item.get("slug"), (item.get("title") or "").strip()
    if not slug or not title:
        return None
    address = item.get("address") or {}
    place = (address.get("formatted") or "").strip()
    # The geocoder files "Bray" under Berkshire, so the county it returns is not
    # trusted; the country code is.
    if (address.get("countryCode") or "ie").lower() == "ie":
        location = "Ireland" if place.lower() in ("", "nationwide", "ireland") else f"{place}, Ireland"
    else:
        location = ", ".join(filter(None, [place, address.get("country")]))
    company = item.get("company") or {}
    types = [t.get("name") for t in item.get("jobTypes") or [] if isinstance(t, dict) and t.get("name")]
    posted = item.get("postedAt")
    try:
        posted_at = date_parser.parse(posted) if posted else None
    except (ValueError, OverflowError):
        posted_at = None
    return RawJob(
        source_job_id=str(item.get("_id") or item.get("id") or slug),
        title=title,
        url=f"{ORIGIN}/job/{slug}",
        location_raw=location,
        description=item.get("description") or None,
        posted_at=posted_at,
        employment_type=", ".join(types) or None,
        company_name=(company.get("name") or "").strip() or None,
    )


class JobAlertAdapter(BaseAdapter):
    """Every open advert on JobAlert.ie, filed under the employer that posted it.

    The slug is unused beyond naming the source; there is one board.
    """

    name = "jobalert"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        jobs: dict[str, RawJob] = {}
        total = 0
        for page in range(1, MAX_PAGES + 1):
            response = client.get(PAGE.format(page=page))
            response.raise_for_status()
            items, total = page_jobs(response.text)
            if not items:
                break
            for item in items:
                job = to_raw_job(item)
                if job:
                    jobs.setdefault(job.source_job_id, job)
            self.polite_pause()
        else:
            return PartialJobs(jobs.values())

        # A read that stopped well short of the board's own count lost pages on the way,
        # and absence from it says nothing about the adverts it missed.
        if total and len(jobs) < total * 0.8:
            return PartialJobs(jobs.values())
        return list(jobs.values())


register(JobAlertAdapter())
