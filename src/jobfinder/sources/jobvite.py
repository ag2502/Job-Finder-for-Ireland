"""Jobvite careers page adapter.

    GET https://jobs.jobvite.com/{slug}/jobs?p={n}
    GET https://jobs.jobvite.com/{slug}/job/{id}

Jobvite has no public API, but every company's hosted board is server-rendered from one
of two templates: a row per role, its title linked to the role's page and its office in
`jv-job-list-location` ("Cork, Ireland"). Pages are numbered from zero and the list
simply runs out; a page that adds nothing new ends the read. Each role's page carries a
JobPosting JSON-LD block with the advert and the posting date.

Boards are global (BioMarin's lists 76 roles, 14 of them Irish), so an advert is opened
only for a role that could be in Ireland: one whose office is Irish, or a multi-office
role whose list entry does not say which. Every role is still returned, so the board is
read whole and a role that closes is closed.

The slug is the company's path on the board: `biomarin`.
"""

from __future__ import annotations

import re

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import iter_ld_objects

HOST = "https://jobs.jobvite.com"
MAX_PAGES = 40
JOB_ID = re.compile(r"/job/([A-Za-z0-9]+)")
MULTI_OFFICE = re.compile(r"\b\d+\s+locations?\b|\bmultiple\b", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_list(page: str) -> list[tuple[str, str, str, str | None]]:
    """(id, url, title, office) for every role in one page of the board.

    Two templates are in use: a table with the title linked in its cell, and (Xperi's)
    a list whose whole row is the link, with the title and office in divs inside it.
    """
    roles = []
    for row in HTMLParser(page).css("tr, li"):
        name = row.css_first(".jv-job-list-name")
        link = row.css_first('a[href*="/job/"]')
        if name is None or link is None:
            continue
        href = link.attributes.get("href") or ""
        job_id = JOB_ID.search(href)
        title = _clean(name.text())
        if job_id is None or not title:
            continue
        office = row.css_first(".jv-job-list-location")
        place = _clean(office.text()) if office is not None else ""
        roles.append((job_id.group(1), HOST + href if href.startswith("/") else href,
                      title, place or None))
    return roles


def parse_role(page: str) -> tuple[str | None, object]:
    """The advert and posting date from a role page's JobPosting JSON-LD."""
    for obj in iter_ld_objects(page):
        if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
            posted = None
            if obj.get("datePosted"):
                try:
                    posted = dateparser.parse(str(obj["datePosted"]))
                except (ValueError, OverflowError):
                    posted = None
            return obj.get("description"), posted
    return None, None


def _may_be_irish(place: str | None) -> bool:
    return not place or bool(MULTI_OFFICE.search(place)) or normalize_location(place).is_ireland


class JobviteAdapter(BaseAdapter):
    name = "jobvite"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        found: dict[str, tuple[str, str, str | None]] = {}
        complete = False
        for number in range(MAX_PAGES):
            response = client.get(f"{HOST}/{slug}/jobs", params={"p": number})
            response.raise_for_status()
            if number == 0 and "jv-job-list" not in response.text:
                # An unknown company lands on Jobvite's own error page.
                raise ValueError(f"no Jobvite job list at {HOST}/{slug}/jobs")
            before = len(found)
            for job_id, url, title, place in parse_list(response.text):
                found.setdefault(job_id, (url, title, place))
            if len(found) == before:
                complete = True
                break
            self.polite_pause()

        jobs: list[RawJob] = []
        for job_id, (url, title, place) in found.items():
            description, posted = None, None
            if _may_be_irish(place):
                try:
                    detail = client.get(url)
                    detail.raise_for_status()
                    description, posted = parse_role(detail.text)
                except httpx.HTTPError:
                    # The list names the role and its office; the advert is a bonus.
                    pass
                self.polite_pause()
            jobs.append(RawJob(
                source_job_id=job_id, title=title, url=url, location_raw=place,
                description=description, posted_at=posted,
            ))
        return jobs if complete else PartialJobs(jobs)


register(JobviteAdapter())
