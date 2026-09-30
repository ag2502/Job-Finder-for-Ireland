"""JazzHR careers page adapter.

    GET https://{slug}.applytojob.com/apply
    GET https://{slug}.applytojob.com/apply/{code}/{title-slug}

JazzHR publishes no public API, but every tenant's careers page is the same
server-rendered template: one `li.list-group-item` per role, its title linked to the
role's page and the office in the `fa-map-marker` line under it ("Dublin, Dublin,
Ireland"). The whole board is on that one page. Each role's page carries a JobPosting
JSON-LD block with the advert and the posting date.

"General expression of interest" entries are talent-pool sign-ups, not vacancies, so
they are left out.

The slug is the subdomain: `horizonquantum`.
"""

from __future__ import annotations

import re

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import iter_ld_objects

CODE = re.compile(r"/apply/([A-Za-z0-9]+)/")
NOT_A_VACANCY = re.compile(r"expression of interest|talent community", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_board(page: str) -> list[tuple[str, str, str, str | None, str | None]]:
    """(code, url, title, location, department) for every role on the careers page."""
    roles = []
    for item in HTMLParser(page).css("li.list-group-item"):
        link = item.css_first(".list-group-item-heading a[href]")
        if link is None:
            continue
        url = (link.attributes.get("href") or "").strip()
        title = _clean(link.text())
        code = CODE.search(url)
        if code is None or not title or NOT_A_VACANCY.search(title):
            continue
        location = department = None
        for detail in item.css(".list-group-item-text li"):
            icon = detail.css_first("i")
            classes = icon.attributes.get("class") or "" if icon is not None else ""
            if "fa-map-marker" in classes:
                location = _clean(detail.text()) or None
            elif "fa-sitemap" in classes:
                department = _clean(detail.text()) or None
        roles.append((code.group(1), url, title, location, department))
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


class JazzHrAdapter(BaseAdapter):
    name = "jazzhr"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"https://{slug}.applytojob.com/apply")
        response.raise_for_status()
        if "list-group" not in response.text:
            # Not a JazzHR careers page: an unknown tenant lands on JazzHR's own site.
            raise ValueError(f"no JazzHR job list at {slug}.applytojob.com")

        jobs: list[RawJob] = []
        for code, url, title, location, department in parse_board(response.text):
            description, posted = None, None
            try:
                detail = client.get(url)
                detail.raise_for_status()
                description, posted = parse_role(detail.text)
            except httpx.HTTPError:
                # The list names the role and its office; the advert is a bonus.
                pass
            self.polite_pause()
            jobs.append(RawJob(
                source_job_id=code, title=title, url=url, location_raw=location,
                description=description, posted_at=posted, department=department,
            ))
        return jobs


register(JazzHrAdapter())
