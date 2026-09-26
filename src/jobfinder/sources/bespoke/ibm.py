"""IBM careers adapter.

careers.ibm.com is a client-side app over IBM's site search, an Elasticsearch query
endpoint that answers without a key:

    POST https://www-api.ibm.com/search/api/v2
         {"appId": "careers", "scopes": ["careers2"],
          "post_filter": {"term": {"field_keyword_05": "Ireland"}}, "from": 0, "size": 100}

The fields are generic names: `field_keyword_05` is the country, `_19` the office
("Mulhuddart, IE"), `_08` the job family, `_17` the work arrangement ("Hybrid") and
`_18` the level. `dcdate` is the posting date. An unrecognised country is not an
error but an empty result, and IBM always has Irish roles, so none is refused.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www-api.ibm.com/search/api/v2"
PAGE_SIZE = 100
MAX_PAGES = 20
FIELDS = [
    "_id", "title", "url", "description", "dcdate",
    "field_keyword_05", "field_keyword_08", "field_keyword_17", "field_keyword_19",
]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.parse(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class IBMAdapter(BaseAdapter):
    name = "ibm"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        country = slug or "Ireland"
        found: dict[str, dict] = {}
        total: int | None = None

        for page in range(MAX_PAGES):
            response = client.post(
                SEARCH_URL,
                json={
                    "appId": "careers",
                    "scopes": ["careers2"],
                    "query": {"bool": {"must": []}},
                    "post_filter": {"term": {"field_keyword_05": country}},
                    "from": page * PAGE_SIZE,
                    "size": PAGE_SIZE,
                    "sort": [{"dcdate": "desc"}, {"_score": "desc"}],
                    "lang": "zz",
                    "localeSelector": {},
                    "sm": {"query": "", "lang": "zz"},
                    "_source": FIELDS,
                },
            )
            response.raise_for_status()
            hits = response.json().get("hits") or {}
            if total is None:
                total = int((hits.get("total") or {}).get("value") or 0)
                if total == 0:
                    raise ValueError(f"ibm returned no roles in {country!r} - filter changed?")

            batch = hits.get("hits") or []
            for hit in batch:
                source = hit.get("_source") or {}
                if source.get("url"):
                    found.setdefault(source["url"], source)
            if not batch or len(found) >= total:
                break
            self.polite_pause()

        return [self._build(source) for source in found.values()]

    @staticmethod
    def _build(source: dict) -> RawJob:
        url = source["url"]
        job_id = url.rsplit("jobId=", 1)[-1] if "jobId=" in url else url
        office = source.get("field_keyword_19")
        arrangement = source.get("field_keyword_17")
        location = office
        if office and arrangement and arrangement.lower() == "remote":
            location = f"{office} (Remote)"
        return RawJob(
            source_job_id=str(job_id),
            title=source.get("title") or "",
            url=url,
            location_raw=location or source.get("field_keyword_05"),
            description=source.get("description"),
            posted_at=_parse_date(source.get("dcdate")),
            department=source.get("field_keyword_08"),
        )


register(IBMAdapter())
