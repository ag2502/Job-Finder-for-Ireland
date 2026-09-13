"""SAP SuccessFactors Recruiting Marketing (RMK) adapter.

    https://{host}/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow={n}

RMK powers the careers sites of AIB, Ryanair, Lidl, Glanbia, SAP, Ericsson, BT and a long
tail of Irish employers, almost always on the employer's own domain. Every tenant renders
the same search-results template, so one parser reads them all.

The obvious source is the wrong one. RMK also publishes an RSS feed, but it stops at 20
items with no way to page past them; a board of 226 read through it would look like a
board of 20 and the reconciler would close the other 206. The search pages are paged,
and state their own total ("Results 1 – 15 of 226"), so the read is checked against it.

Pages are 15 or 25 rows depending on tenant configuration, so the step is taken from the
first page rather than assumed.
"""

from __future__ import annotations

import html
import re

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

MAX_PAGES = 160
COMPLETENESS = 0.95

TITLE_ANCHOR = re.compile(r"<a\b([^>]*jobTitle-link[^>]*)>(.*?)</a>", re.I | re.S)
HREF = re.compile(r'href="([^"]+)"', re.I)
# Classic RMK puts the location in a `jobLocation` span; Career Site Builder tiles put it
# in a labelled section field whose id ends `-section-<field>-value`.
LOCATION = re.compile(
    r'<span\b[^>]*class="jobLocation[^"]*"[^>]*>(.*?)</span>'
    r'|-section-(?:location|city|multilocation)[a-z-]*-value"[^>]*>(.*?)</(?:span|div)>',
    re.I | re.S,
)
TOTALS = (
    re.compile(r"of\s*<b>\s*([\d,.]+)\s*</b>", re.I),
    re.compile(r"totalCount\"?\s*[:=]\s*\"?(\d+)", re.I),
)
JOB_ID = re.compile(r"/(\d+)/?(?:\?|$)")
TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAGS.sub(" ", fragment))).strip()


def parse_page(page: str, host: str) -> tuple[list[RawJob], int | None]:
    """Jobs on one search-results page, and the board total the page states.

    Read from the title links rather than from table rows, because tenants render one of
    two layouts: classic RMK tables (`tr.data-row`) and Career Site Builder tiles, which
    have no rows at all. Both use the same `jobTitle-link` anchor.
    """
    total = None
    for pattern in TOTALS:
        match = pattern.search(page)
        if match:
            total = int(re.sub(r"[,.]", "", match.group(1)))
            break

    anchors: list[tuple[int, int, str, str, str]] = []
    for match in TITLE_ANCHOR.finditer(page):
        href_match = HREF.search(match.group(1))
        if not href_match:
            continue
        href = html.unescape(href_match.group(1))
        id_match = JOB_ID.search(href)
        if id_match:
            anchors.append((match.start(), match.end(), id_match.group(1), href, _clean(match.group(2))))

    jobs: list[RawJob] = []
    seen: set[str] = set()
    for index, (_start, end, job_id, href, title) in enumerate(anchors):
        if job_id in seen or not title:
            continue
        seen.add(job_id)

        # A job's fields sit between its title and the next *different* job's title; the
        # phone layout repeats the same title link in between, so that one is skipped.
        stop = next((a[0] for a in anchors[index + 1 :] if a[2] != job_id), len(page))
        location_match = LOCATION.search(page, end, stop)
        location = None
        if location_match:
            location = _clean(location_match.group(1) or location_match.group(2) or "") or None

        jobs.append(
            RawJob(
                source_job_id=job_id,
                title=title,
                url=href if href.startswith("http") else f"https://{host}{href}",
                location_raw=location,
            )
        )
    return jobs, total


class SuccessFactorsAdapter(BaseAdapter):
    name = "successfactors"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host = slug.removeprefix("https://").removeprefix("http://").rstrip("/")

        jobs: dict[str, RawJob] = {}
        total: int | None = None
        startrow = 0
        step: int | None = None
        hit_ceiling = False
        repeated = False

        for page_number in range(MAX_PAGES + 1):
            if page_number == MAX_PAGES:
                hit_ceiling = True
                break
            response = client.get(
                f"https://{host}/search/",
                params={
                    "q": "",
                    "sortColumn": "referencedate",
                    "sortDirection": "desc",
                    "startrow": startrow,
                },
            )
            response.raise_for_status()
            page_jobs, page_total = parse_page(response.text, host)

            if total is None:
                total = page_total
            if not page_jobs:
                break

            before = len(jobs)
            for job in page_jobs:
                jobs.setdefault(job.source_job_id, job)
            if len(jobs) == before:
                # The same page came back again: the tenant ignores `startrow`.
                repeated = True
                break

            step = step or len(page_jobs)
            startrow += step
            if total is not None and len(jobs) >= total:
                break
            self.polite_pause()

        if not jobs:
            raise ValueError(f"no SuccessFactors job rows found on {host}")
        if repeated and total is None:
            # Career Site Builder pages state no total. A page that repeats could be a
            # one-page board or a tenant that ignores paging, and nothing on the page
            # says which - so the read is kept but never used to close anything.
            return PartialJobs(jobs.values())
        if hit_ceiling:
            # A board larger than the page ceiling (SAP runs to thousands) is read newest
            # first up to the ceiling. That is a sample: reported as one, so the jobs read
            # are kept current and the unread tail is never closed on their account.
            return PartialJobs(jobs.values())
        if total and len(jobs) < total * COMPLETENESS:
            raise ValueError(
                f"SuccessFactors read {len(jobs)} of {total} jobs from {host}; "
                "refusing to report an incomplete board"
            )
        return list(jobs.values())


register(SuccessFactorsAdapter())
