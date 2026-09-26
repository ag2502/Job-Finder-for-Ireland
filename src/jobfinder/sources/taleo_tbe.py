"""Taleo Business Edition career-section adapter.

TBE career sections render their open requisitions on a server-side page:

    GET https://{host}/{dc}/ats/careers/v2/searchResults?org={ORG}&cws={section}

Each requisition is an accordion block: a `viewJobLink` anchor with the title and the
link, then the location and the requisition id. The list scrolls ten at a time: each
page ends with a `jscroll-next` link (`?next&rowFrom=10`) that continues the same
session, so the pages are followed with one client until there is no next link.

The slug is ``host/dc|ORG|section``, e.g. ``lde.tbe.taleo.net/lde02|ARNOTTS|79``. An
employer may run several sections (Brown Thomas Arnotts has one per brand), each its
own source.
"""

from __future__ import annotations

import html
import re

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

_BLOCK = re.compile(
    r'<a href="([^"]*viewRequisition[^"]*?rid=(\d+)[^"]*)" class="viewJobLink">(.*?)</a>\s*</h4>(.*?)</div>\s*<!--/\.accordion-head-info',
    re.S,
)
_DIV = re.compile(r"<div[^>]*>(.*?)</div>", re.S)
_NEXT = re.compile(r'<a href="([^"]*searchResults\?next[^"]*)" class="jscroll-next"')
MAX_PAGES = 50


def split_slug(slug: str) -> tuple[str, str, str]:
    base, org, section = (slug.split("|") + ["", ""])[:3]
    if not (base and org and section):
        raise ValueError(f"Taleo TBE slug must be 'host/dc|ORG|section', got {slug!r}")
    return base, org, section


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


class TaleoTBEAdapter(BaseAdapter):
    name = "taleo_tbe"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        base, org, section = split_slug(slug)
        response = client.get(
            f"https://{base}/ats/careers/v2/searchResults", params={"org": org, "cws": section}
        )
        response.raise_for_status()
        if "oracletaleocwsv2" not in response.text:
            raise ValueError(f"no Taleo career section at {slug!r}")

        jobs: dict[str, RawJob] = {}
        page = response.text
        for _ in range(MAX_PAGES):
            before = len(jobs)
            for url, rid, title, rest in _BLOCK.findall(page):
                fields = [_clean(value) for value in _DIV.findall(rest)]
                location = next((f for f in fields if f and f != rid), None)
                jobs.setdefault(rid, RawJob(
                    source_job_id=rid,
                    title=_clean(title),
                    url=html.unescape(url),
                    location_raw=location,
                ))
            following = _NEXT.search(page)
            if not following or len(jobs) == before:
                return list(jobs.values())
            self.polite_pause()
            host = base.split("/", 1)[0]
            response = client.get(f"https://{host}{html.unescape(following.group(1))}")
            response.raise_for_status()
            page = response.text
        return PartialJobs(jobs.values())


register(TaleoTBEAdapter())
