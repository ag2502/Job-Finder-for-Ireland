"""CandidateManager recruitment portal adapter.

    https://www.candidatemanager.net/cm/p/pJobs.aspx?mid={mid}&sid={sid}

CandidateManager hosts the vacancy boards of Irish semi-state and public bodies — the
Central Bank of Ireland, EirGrid and RTÉ among them — which is why none of their careers
pages carry a recognisable ATS fingerprint: the board lives on this shared host and is
linked, not embedded.

Each board is addressed by two opaque codes, so the slug is ``mid|sid``. The page is
server-rendered: one table listing every open vacancy with its reference, job type,
category and location, and no pagination on any board seen. A pager, should one ever
appear, would make the page a sample, so it is reported as partial rather than risk
closing the vacancies on the pages not read.
"""

from __future__ import annotations

import html
import re

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

LISTING = "https://www.candidatemanager.net/cm/p/pJobs.aspx"
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
HEADER = re.compile(r"<th\b[^>]*>(.*?)</th>", re.I | re.S)
DETAIL = re.compile(r"<a\b[^>]*href=\"([^\"]*pJobDetails\.aspx\?[^\"]*)\"[^>]*>(.*?)</a>", re.I | re.S)
JOB_ID = re.compile(r"[?&]jid=([A-Za-z0-9]+)")
REFERENCE = re.compile(r"<small\b[^>]*>\s*\(([^)]+)\)\s*</small>", re.I)
PAGER = re.compile(r"__doPostBack\([^)]*Page\$|class=\"[^\"]*pagination", re.I)
TAGS = re.compile(r"<[^>]+>")

URL_CODES = re.compile(r"candidatemanager\.net/cm/p/(?:pJobs|pJobDetails)\.aspx\?[^\"'\s<>]*?mid=([A-Za-z0-9*]+)&(?:amp;)?sid=([A-Za-z0-9*%{}]+)", re.I)


def slug_from_url(url: str) -> str | None:
    """``mid|sid`` from any CandidateManager listing or job-detail URL."""
    match = URL_CODES.search(url)
    return f"{match.group(1)}|{match.group(2)}" if match else None


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", fragment))).strip()


def split_slug(slug: str) -> tuple[str, str]:
    mid, _, sid = slug.partition("|")
    if not mid or not sid:
        raise ValueError(f"CandidateManager slug must be 'mid|sid', got {slug!r}")
    return mid, sid


class CandidateManagerAdapter(BaseAdapter):
    name = "candidatemanager"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        mid, sid = split_slug(slug)
        response = client.get(LISTING, params={"mid": mid, "sid": sid})
        response.raise_for_status()
        page = response.text

        # Boards choose their own columns and order (EirGrid: type, category, location;
        # RTÉ puts working pattern where EirGrid has category), so columns are found by
        # header name rather than position.
        header = next((r for r in ROW.findall(page) if "<th" in r.lower()), "")
        names = [_clean(h).lower() for h in HEADER.findall(header)]
        location_at = next((i for i, n in enumerate(names) if "location" in n), None)
        category_at = next((i for i, n in enumerate(names) if "category" in n or "department" in n), None)

        jobs: dict[str, RawJob] = {}
        for row in ROW.findall(page):
            link = DETAIL.search(row)
            if not link:
                continue
            href = html.unescape(link.group(1))
            job_id = JOB_ID.search(href)
            title = _clean(link.group(2))
            if not job_id or not title:
                continue

            cells = [_clean(cell) for cell in CELL.findall(row)]
            reference = REFERENCE.search(row)
            location = (
                cells[location_at] or None
                if location_at is not None and location_at < len(cells)
                else None
            )
            category = (
                cells[category_at] or None
                if category_at is not None and category_at < len(cells)
                else None
            )

            jobs.setdefault(
                job_id.group(1),
                RawJob(
                    source_job_id=job_id.group(1),
                    title=title,
                    url=href,
                    location_raw=location,
                    department=category,
                    description=f"Reference {reference.group(1)}" if reference else None,
                ),
            )

        if not jobs:
            # A board with no vacancies still renders its table header; a page with no
            # table at all is a broken or retired board, which must not close anything.
            if "Current Vacancies" in page or "<table" in page.lower():
                return []
            raise ValueError(f"no CandidateManager vacancy table for {slug!r}")

        if PAGER.search(page):
            return PartialJobs(jobs.values())
        return list(jobs.values())


register(CandidateManagerAdapter())
