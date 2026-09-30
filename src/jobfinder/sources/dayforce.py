"""Dayforce careers site adapter.

    GET  https://jobs.dayforcehcm.com/api/auth/csrf
    POST https://jobs.dayforcehcm.com/api/geo/{namespace}/jobposting/search
         {"clientNamespace", "jobBoardCode", "cultureCode", "paginationStart"}

A Dayforce careers site (`jobs.dayforcehcm.com/en-IE/prometric/prometriccareersite`)
is a single-page app over a JSON search. The search wants the CSRF token the site
hands out first, and answers with each posting's advert, its offices
(`postingLocations`, full addresses such as "Dundalk, Co. Louth, Ireland") and when it
went up, so one call per page reads everything. `maxCount` says how many there are;
the next page starts where this one ended.

The slug is the client namespace and the job board code from the careers URL:
`prometric|prometriccareersite`.
"""

from __future__ import annotations

import re

import httpx
from dateutil import parser as dateparser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

HOST = "https://jobs.dayforcehcm.com"
CULTURE = "en-US"
MAX_PAGES = 40


_CAREERS_URL = re.compile(
    r"jobs\.dayforcehcm\.com/(?:[a-z]{2}-[a-z]{2}/)?([a-z0-9_-]+)/([a-z0-9_-]+)", re.I
)
_NOT_A_BOARD = {"jobs", "api", "_next", "candidateportal"}


def slug_from_url(text: str) -> str | None:
    """The `namespace|board` slug from a careers link, or None without a board code."""
    for match in _CAREERS_URL.finditer(text):
        namespace, board = match.group(1), match.group(2)
        if namespace.lower() not in _NOT_A_BOARD and board.lower() not in _NOT_A_BOARD:
            return f"{namespace}|{board}"
    return None


def split_slug(slug: str) -> tuple[str, str]:
    namespace, _, board = slug.partition("|")
    if not (namespace and board):
        raise ValueError(f"dayforce slug must be 'namespace|jobBoardCode', got {slug!r}")
    return namespace, board


class DayforceAdapter(BaseAdapter):
    name = "dayforce"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        namespace, board = split_slug(slug)
        token = client.get(f"{HOST}/api/auth/csrf")
        token.raise_for_status()
        headers = {"x-csrf-token": token.json().get("csrfToken", ""),
                   "Content-Type": "application/json"}

        postings: dict[str, dict] = {}
        total: int | None = None
        start = 0
        for _ in range(MAX_PAGES):
            response = client.post(
                f"{HOST}/api/geo/{namespace}/jobposting/search",
                json={"clientNamespace": namespace, "jobBoardCode": board,
                      "cultureCode": CULTURE, "distanceUnit": 1, "paginationStart": start},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("jobPostings") or []
            if total is None:
                total = payload.get("maxCount") or 0
            before = len(postings)
            for item in page:
                if item.get("jobPostingId") is not None:
                    postings.setdefault(str(item["jobPostingId"]), item)
            start += len(page)
            if not page or len(postings) == before or start >= total:
                break
            self.polite_pause()

        jobs: list[RawJob] = []
        for posting_id, item in postings.items():
            places = [loc.get("formattedAddress") for loc in item.get("postingLocations") or []
                      if loc.get("formattedAddress")] or [None]
            posted = None
            if item.get("postingStartTimestampUTC"):
                try:
                    posted = dateparser.parse(item["postingStartTimestampUTC"])
                except (ValueError, OverflowError):
                    posted = None
            jobs.append(RawJob(
                source_job_id=posting_id,
                title=item.get("jobTitle") or "",
                url=f"{HOST}/{CULTURE}/{namespace}/{board}/jobs/{posting_id}",
                location_raw=places[0],
                extra_locations=places[1:],
                description=item.get("jobDescription"),
                posted_at=posted,
            ))
        return jobs if len(postings) >= (total or 0) else PartialJobs(jobs)


register(DayforceAdapter())
