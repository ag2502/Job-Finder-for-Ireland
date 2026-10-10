"""TalentBrew (Radancy) careers site adapter.

    GET https://{host}/location/{country}-jobs/{org}/{geo}/{level}
    GET https://{host}/job/{city}/{title-slug}/{org}/{id}

TalentBrew builds the branded careers sites of Synopsys, UnitedHealth Group and many
others. Their search (`/search-jobs/`) is usually closed to crawlers by robots.txt,
but every site also publishes a location page per country, listed in its own sitemap,
and that page is plain HTML: a `#search-results-list` of roles, each linked to its own
page with the office in a `job-location` line, and the list's size in
`data-total-results`. Each role's page carries JobPosting JSON-LD for the advert and
the posting date.

A location page shows its first page of results only, since the next pages come from
the closed search: UnitedHealth's shows 15 of its 41 Irish roles. The rest are in the
site's sitemap, whose role URLs name their city (`/job/dublin/...`), so a longer list is
completed from the sitemap's roles in the cities the page itself lists, each read from
its JSON-LD. If that still falls short, the read is reported as partial, so nothing is
closed for having been on a page the crawler could not reach.

The slug is the country's location page, from the site's sitemap.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import iter_ld_objects, parse_job_posting

TOTAL = re.compile(r'data-total-results="(\d+)"')
JOB_ID = re.compile(r"/job/[^\"'\s]*?/(\d+)/?$")
CITY = re.compile(r"/job/([^/]+)/")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
MAX_SITEMAP_ROLES = 200


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_list(page: str, base: str) -> tuple[list[tuple[str, str, str, str | None]], int | None]:
    """(id, url, title, office) for each role listed, and the stated total."""
    tree = HTMLParser(page)
    results = tree.css_first("#search-results-list")
    roles = []
    for item in results.css("li") if results is not None else []:
        link = item.css_first('a[href*="/job/"]')
        if link is None:
            continue
        href = link.attributes.get("href") or ""
        job_id = link.attributes.get("data-job-id") or (JOB_ID.search(href) or [None, None])[1]
        # IKEA's template names its parts job-list__title and job-list__location.
        heading = link.css_first("h2, h3, .job-list__title")
        title = _clean((heading or link).text())
        if not job_id or not title:
            continue
        office_node = item.css_first(".job-location, .job-list__location")
        office = _clean(office_node.text()) if office_node is not None else None
        # A card naming only the country ("Ireland") leaves the town to the URL,
        # /job/sligo/...
        city = CITY.search(href)
        if office and "," not in office and city:
            office = f"{city.group(1).replace('-', ' ').title()}, {office}"
        roles.append((job_id, urljoin(base, href), title, office))
    total = TOTAL.search(page)
    return roles, int(total.group(1)) if total else None


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


class TalentBrewAdapter(BaseAdapter):
    name = "talentbrew"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        url = slug if slug.startswith("http") else "https://" + slug
        response = client.get(url)
        response.raise_for_status()
        if "search-results-list" not in response.text:
            raise ValueError(f"no TalentBrew results list at {url}")
        roles, total = parse_list(response.text, str(response.url))

        jobs: list[RawJob] = []
        seen: set[str] = set()
        for job_id, link, title, office in roles:
            if job_id in seen:
                continue
            seen.add(job_id)
            description, posted = None, None
            try:
                detail = client.get(link)
                detail.raise_for_status()
                description, posted = parse_role(detail.text)
            except httpx.HTTPError:
                # The list names the role and its office; the advert is a bonus.
                pass
            self.polite_pause()
            jobs.append(RawJob(
                source_job_id=job_id, title=title, url=link, location_raw=office,
                description=description, posted_at=posted,
            ))
        if total is not None and len(jobs) < total:
            jobs.extend(self._from_sitemap(url, roles, seen, client))
        if total is not None and len(jobs) < total:
            return PartialJobs(jobs)
        return jobs

    def _from_sitemap(
        self, url: str, roles: list, seen: set[str], client: httpx.Client
    ) -> list[RawJob]:
        """The roles in the page's own cities that the page did not have room for."""
        cities = {m.group(1) for _, link, _, _ in roles if (m := CITY.search(link))}
        if not cities:
            return []
        try:
            sitemap = client.get(urljoin(url, "/sitemap.xml"))
            sitemap.raise_for_status()
        except httpx.HTTPError:
            return []
        extra: list[RawJob] = []
        for link in LOC.findall(sitemap.text)[:50000]:
            city, job_id = CITY.search(link), JOB_ID.search(link)
            if not (city and job_id) or city.group(1) not in cities or job_id.group(1) in seen:
                continue
            if len(extra) >= MAX_SITEMAP_ROLES:
                break
            seen.add(job_id.group(1))
            try:
                detail = client.get(link)
                detail.raise_for_status()
            except httpx.HTTPError:
                continue
            for obj in iter_ld_objects(detail.text):
                if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                    job = parse_job_posting(obj, link)
                    if job is not None:
                        job.source_job_id = job_id.group(1)
                        extra.append(job)
                    break
            self.polite_pause()
        return extra


register(TalentBrewAdapter())
