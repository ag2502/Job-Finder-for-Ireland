"""Revolut careers adapter.

Revolut hires through its own site, which is rendered on the server with every open
position embedded as Next.js page data:

    https://www.revolut.com/careers/
        __NEXT_DATA__ -> props.pageProps.positions[{id, text, team, locations[]}]

Each location names its country, and a remote role is listed as its own location
("Ireland - Remote"), so the Irish roles are the ones with any location in Ireland.
The list carries no description; each Irish role's page does, under the same key.

The whole list arrives in one page, so an empty list is a changed page rather than an
empty board, and fails.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

CAREERS_URL = "https://www.revolut.com/careers/"
POSITION_URL = "https://www.revolut.com/careers/position/{id}/"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def page_props(html: str) -> dict:
    match = _NEXT_DATA.search(html)
    if not match:
        raise ValueError("revolut page carried no __NEXT_DATA__ - markup changed")
    return (json.loads(match.group(1)).get("props") or {}).get("pageProps") or {}


def _place(location: dict) -> str:
    name, country = location.get("name") or "", location.get("country") or ""
    if location.get("type") == "remote":
        return f"Remote, {country}" if country else "Remote"
    return ", ".join(part for part in (name, country) if part)


class RevolutAdapter(BaseAdapter):
    name = "revolut"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        country = slug or "Ireland"
        response = client.get(CAREERS_URL, headers=BROWSER_HEADERS)
        response.raise_for_status()
        positions = page_props(response.text).get("positions")
        if not isinstance(positions, list) or not positions:
            raise ValueError("revolut careers page listed no positions - markup changed")

        jobs: list[RawJob] = []
        for position in positions:
            locations = [loc for loc in position.get("locations") or [] if isinstance(loc, dict)]
            local = [loc for loc in locations if loc.get("country") == country]
            if not local or not position.get("id"):
                continue
            # The Irish offices lead, so the posting reads as the Irish role it is here.
            ordered = local + [loc for loc in locations if loc not in local]
            places = [_place(loc) for loc in ordered]
            jobs.append(
                RawJob(
                    source_job_id=str(position["id"]),
                    title=position.get("text") or "",
                    url=POSITION_URL.format(id=position["id"]),
                    location_raw=places[0],
                    extra_locations=places[1:],
                    description=self._description(client, str(position["id"])),
                    department=position.get("team"),
                )
            )
        return jobs

    def _description(self, client: httpx.Client, position_id: str) -> str | None:
        try:
            response = client.get(POSITION_URL.format(id=position_id), headers=BROWSER_HEADERS)
            response.raise_for_status()
            return (page_props(response.text).get("position") or {}).get("description")
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("revolut position %s failed: %s", position_id, exc)
            return None
        finally:
            self.polite_pause()


register(RevolutAdapter())
