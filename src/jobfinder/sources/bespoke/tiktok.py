"""TikTok careers adapter.

TikTok's careers site (lifeattiktok.com) is a client-side app over a public search API:

    POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts
         {"location_code_list": ["CT_37"], "limit": 100, "offset": 0, ...}

The list response already carries each role's full description and requirements, so
one request per hundred roles is the whole cost.

Locations are filtered by TikTok's own city codes: `CT_37` is Dublin. Only city codes
filter; the country and region codes above it (`CN_9` Ireland, `ST_22` County Dublin)
return nothing, and an unknown code is indistinguishable from an empty board. The
empty answer is therefore refused rather than trusted, since an OK fetch of nothing
would close every TikTok role in Dublin.
"""

from __future__ import annotations

import logging

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
JOB_URL = "https://lifeattiktok.com/search/{id}"
PAGE_SIZE = 100
MAX_PAGES = 20
IMPLAUSIBLE_COUNT = 2000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Origin": "https://lifeattiktok.com",
    "Referer": "https://lifeattiktok.com/",
    # Selects TikTok's own board on the API shared with ByteDance's other brands.
    "website-path": "tiktok",
}


def _place(city: dict | None) -> str | None:
    """"Dublin, Ireland" from the nested city > region > country chain."""
    names: list[str] = []
    node = city
    while isinstance(node, dict):
        name = node.get("en_name")
        if name and name not in names:
            names.append(name)
        node = node.get("parent")
    if len(names) > 2:
        names = [names[0], names[-1]]  # city and country; the region repeats the city
    return ", ".join(names) or None


class TikTokAdapter(BaseAdapter):
    name = "tiktok"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        codes = [code for code in (slug or "CT_37").split(",") if code]
        found: dict[str, dict] = {}
        count: int | None = None
        offset = 0

        for _ in range(MAX_PAGES):
            response = client.post(
                SEARCH_URL,
                json={
                    "keyword": "",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "location_code_list": codes,
                    "recruitment_id_list": [],
                    "job_category_id_list": [],
                    "subject_id_list": [],
                },
                headers=HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise ValueError(f"tiktok search refused: {payload.get('message')!r}")

            data = payload.get("data") or {}
            if count is None:
                count = int(data.get("count") or 0)
                if count == 0:
                    raise ValueError(f"tiktok returned no roles for {codes} - wrong code?")
                if count >= IMPLAUSIBLE_COUNT:
                    raise ValueError(f"tiktok location filter ignored ({count} roles)")

            batch = data.get("job_post_list") or []
            for item in batch:
                if item.get("id"):
                    found.setdefault(str(item["id"]), item)
            offset += len(batch)
            if not batch or offset >= count:
                break
            self.polite_pause()

        return [self._build(item) for item in found.values()]

    @staticmethod
    def _build(item: dict) -> RawJob:
        description = "\n\n".join(
            part for part in (item.get("description"), item.get("requirement")) if part
        )
        category = item.get("job_category") or {}
        return RawJob(
            source_job_id=str(item["id"]),
            title=item.get("title") or "",
            url=JOB_URL.format(id=item["id"]),
            location_raw=_place(item.get("city_info")),
            description=description or None,
            department=category.get("en_name"),
            # The API gives no posting date; first_seen_at stands in for it.
            posted_at=None,
        )


register(TikTokAdapter())
