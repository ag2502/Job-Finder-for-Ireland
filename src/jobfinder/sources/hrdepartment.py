"""HRdepartment ATS adapter.

    GET https://{host}/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_pageSize:100/jobSearchPaginationExternal_page:{n}
    GET https://{host}/hr/ats/Posting/view/{id}

An HRdepartment careers site (Sanmina's is `sanminacareers.mua.hrdepartment.com`) lists
every open role in one server-rendered table: the title linked to the posting, the
requisition id, the department and the office ("Europe: Ireland-Fermoy"). The list
states its own size ("1 - 100 of 979") and is paged by path segments, a hundred rows
at a time, so a read is checked against that total. The location filter is a session
form, which is why the whole list is read instead.

Boards are global, so an advert (`#job_details_ats_requisition_description`, no JSON-LD)
is opened only for a role whose office is in Ireland. Every role is still returned.

The slug is the careers host.
"""

from __future__ import annotations

import re

import httpx
from selectolax.parser import HTMLParser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

PAGE_SIZE = 100
MAX_PAGES = 40
COMPLETENESS = 0.95
POSTING = re.compile(r"/hr/ats/Posting/view/(\d+)")
TOTAL = re.compile(r"\d+\s*-\s*\d+\s*of\s*(\d+)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_page(page: str) -> tuple[list[tuple[str, str, str | None, str | None]], int | None]:
    """(id, title, department, office) for each row, and the list's stated total."""
    roles = []
    for row in HTMLParser(page).css("tbody tr"):
        cells = row.css("td")
        link = row.css_first("a[href]")
        posting = POSTING.search(link.attributes.get("href") or "") if link is not None else None
        if posting is None or len(cells) < 2:
            continue
        department = _clean(cells[2].text()) if len(cells) > 3 else None
        office = _clean(cells[-1].text()) or None
        roles.append((posting.group(1), _clean(link.text()), department or None, office))
    total = TOTAL.search(page)
    return roles, int(total.group(1)) if total else None


def parse_posting(page: str) -> str | None:
    node = HTMLParser(page).css_first("#job_details_ats_requisition_description")
    return node.html if node is not None and node.text(strip=True) else None


class HrDepartmentAdapter(BaseAdapter):
    name = "hrdepartment"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        base = "https://" + slug.removeprefix("https://").strip("/")
        found: dict[str, tuple[str, str | None, str | None]] = {}
        total: int | None = None
        for number in range(1, MAX_PAGES + 1):
            response = client.get(
                f"{base}/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_pageSize:{PAGE_SIZE}"
                f"/jobSearchPaginationExternal_page:{number}"
            )
            response.raise_for_status()
            roles, stated = parse_page(response.text)
            if total is None:
                if stated is None and not roles:
                    raise ValueError(f"no HRdepartment job list at {base}")
                total = stated or 0
            before = len(found)
            for job_id, title, department, office in roles:
                found.setdefault(job_id, (title, department, office))
            if len(found) == before or len(found) >= total:
                break
            self.polite_pause()

        jobs: list[RawJob] = []
        for job_id, (title, department, office) in found.items():
            url = f"{base}/hr/ats/Posting/view/{job_id}"
            description = None
            if office and normalize_location(office).is_ireland:
                try:
                    detail = client.get(url)
                    detail.raise_for_status()
                    description = parse_posting(detail.text)
                except httpx.HTTPError:
                    # The list names the role and its office; the advert is a bonus.
                    pass
                self.polite_pause()
            jobs.append(RawJob(
                source_job_id=job_id, title=title, url=url, location_raw=office,
                description=description, department=department,
            ))
        if total and len(found) < total * COMPLETENESS:
            return PartialJobs(jobs)
        return jobs


register(HrDepartmentAdapter())
