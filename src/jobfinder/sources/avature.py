"""Avature career portal adapter.

    GET https://{host}/{portal}/SearchJobs/?folderOffset={n}
    GET https://{host}/{portal}/FolderDetail/{title-slug}/{id}

An Avature portal (`kpmgireland.avature.net/experiencedhires`) is server-rendered: the
search pages list ten roles each, every one with its title linked to its own page and
the office in the line under it ("Dublin - "). Paging is `folderOffset`, and the list
simply runs out. Each role's page holds the advert under "Description and
Requirements"; its JSON-LD names only the title and date, so the office comes from the
list.

Newer portals (Bloomberg's) lay the list out as `article--result` cards linking to
`JobDetail/{title-slug}/{id}`, the office in a `list-item-location` span. Both layouts
are read.

The slug is the host and portal path: `kpmgireland.avature.net/experiencedhires`. A
global portal takes a keyword after a pipe, `bloomberg.avature.net/careers|Dublin`,
sent as the search box's `search` term so only the Irish roles are listed and opened,
or the portal's own filter instead (`jobs.siemens.com/en_US/externaljobs|42386[]=812128`,
Siemens' country field set to Ireland; the value is found by picking it on the page).
A filter can vouch for a place, named after a second pipe (`...|42386[]=812128|Ireland`):
a row it returns that says only "Multiple Locations" is given that place as an office.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import iter_ld_objects

MAX_PAGES = 50
DETAIL = re.compile(r"/(?:FolderDetail|JobDetail)/(?:[^/?#]+/)?(\d+)")
GENERIC_PLACE = re.compile(r"^(?:multiple locations|\d+ locations?)$", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_search_page(page: str) -> list[tuple[str, str, str | None]]:
    """(url, title, location) for every role listed on one search page."""
    tree = HTMLParser(page)
    roles: list[tuple[str, str, str | None]] = []
    for item in tree.css("li.list__item--hr-bottom, li.list__item, article.article--result"):
        link = item.css_first(
            ".list__item__text__title a[href], .article__header__text__title a[href]"
        )
        if link is None:
            continue
        url = (link.attributes.get("href") or "").strip()
        title = _clean(link.text())
        if not DETAIL.search(url) or not title:
            continue
        subtitle = item.css_first(".list-item-location, .list__item__text__subtitle")
        place = _clean(subtitle.text()).strip(" -|,") if subtitle is not None else ""
        roles.append((url, title, place or None))
    return roles


def parse_detail(page: str) -> tuple[str | None, object]:
    """The advert's text and its posting date, from the role's own page."""
    posted = None
    for obj in iter_ld_objects(page):
        if isinstance(obj, dict) and obj.get("datePosted"):
            try:
                posted = dateparser.parse(str(obj["datePosted"]))
            except (ValueError, OverflowError):
                posted = None
            break
    tree = HTMLParser(page)
    for node in tree.css("script, style, nav, header, footer"):
        node.decompose()
    articles = tree.css("article")
    body = next(
        (a for a in articles if "Description" in (a.text() or "")[:200]),
        articles[-1] if articles else None,
    )
    return (body.html if body is not None else None), posted


class AvatureAdapter(BaseAdapter):
    name = "avature"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        portal, _, rest = slug.partition("|")
        query, _, vouched = rest.partition("|")
        base = "https://" + portal.removeprefix("https://").removeprefix("http://").strip("/")
        if "=" in query:
            search = dict(parse_qsl(query, keep_blank_values=True))
        else:
            search = {"search": query} if query else {}
        found: dict[str, tuple[str, str, str | None]] = {}
        partial = True
        # The page size is the portal's own: KPMG shows ten, Bloomberg twelve, Siemens
        # six. Stepping ten on Siemens skipped four roles in every ten.
        offset, step = 0, None
        for page_number in range(MAX_PAGES):
            response = client.get(
                f"{base}/SearchJobs/", params={**search, "folderOffset": offset},
            )
            response.raise_for_status()
            if page_number == 0 and "SearchJobs" not in response.text:
                # Not an Avature search page: a wrong slug, or the portal has moved.
                # An empty but genuine portal still links its own search.
                raise ValueError(f"no Avature job search at {base}/SearchJobs/")
            roles = parse_search_page(response.text)
            before = len(found)
            for url, title, place in roles:
                found.setdefault(DETAIL.search(url).group(1), (url, title, place))
            if not roles or len(found) == before:
                # Past the last page, or a portal that ignores the offset: either way
                # the list has been read to its end.
                partial = False
                break
            step = step or len(roles)
            offset += step
            self.polite_pause()

        jobs: list[RawJob] = []
        for job_id, (url, title, place) in found.items():
            description, posted = None, None
            try:
                detail = client.get(url)
                detail.raise_for_status()
                description, posted = parse_detail(detail.text)
            except httpx.HTTPError:
                # The list names the role and its office; the advert is a bonus.
                pass
            self.polite_pause()
            vouch = vouched and (not place or GENERIC_PLACE.match(place))
            jobs.append(RawJob(
                source_job_id=job_id, title=title, url=url, location_raw=place,
                description=description, posted_at=posted,
                extra_locations=[vouched] if vouch else [],
            ))
        return PartialJobs(jobs) if partial else jobs


register(AvatureAdapter())
