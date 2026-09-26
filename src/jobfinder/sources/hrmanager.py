"""HR Manager (hr-manager.net) adapter.

HR Manager's job portal widget reads a public JSON list per customer:

    GET https://recruiter-api.hr-manager.net/jobportal.svc/{alias}/positionlist/json/?take=500
        -> {TransactionStatus: {StatusCode: 0}, PositionCountCustomer, Items[...]}

Each item names its location(s), category, publish date and the link a candidate
follows. A non-zero status code is how the API reports an unknown customer, so it fails
the fetch rather than being read as an empty board. Jones Engineering is read this way.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

API_URL = "https://recruiter-api.hr-manager.net/jobportal.svc/{alias}/positionlist/json/"
PAGE_SIZE = 500
_MS_DATE = re.compile(r"/Date\((-?\d+)")
# Titles often end with the place and the project number: "Accountant - Dublin - 145423".
_TITLE_TAIL = re.compile(r"\s+-\s+\d{4,}$")


def _parse_date(value: str | None) -> datetime | None:
    match = _MS_DATE.search(value or "")
    if not match or int(match.group(1)) <= 0:
        return None
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)


def _names(value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [item["Name"] for item in items if isinstance(item, dict) and item.get("Name")]


class HRManagerAdapter(BaseAdapter):
    name = "hrmanager"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(API_URL.format(alias=slug), params={"take": PAGE_SIZE})
        response.raise_for_status()
        payload = response.json()
        status = (payload.get("TransactionStatus") or {}).get("StatusCode")
        if status != 0:
            raise ValueError(f"HR Manager refused customer {slug!r}: {payload.get('TransactionStatus')}")

        items = payload.get("Items") or []
        stated = payload.get("PositionCountCustomer")
        if isinstance(stated, int) and len(items) < stated:
            raise ValueError(f"HR Manager listed {len(items)} of {stated} positions for {slug!r}")

        jobs: list[RawJob] = []
        for item in items:
            if not item.get("Id") or not item.get("Name"):
                continue
            places = _names(item.get("PositionLocationMultiSelection")) or _names(item.get("PositionLocation"))
            categories = _names(item.get("PositionCategory"))
            jobs.append(
                RawJob(
                    source_job_id=str(item["Id"]),
                    title=_TITLE_TAIL.sub("", item["Name"]).strip(),
                    url=item.get("AdvertisementUrlSecure") or item.get("AdvertisementUrl") or "",
                    location_raw=places[0] if places else None,
                    extra_locations=places[1:],
                    posted_at=_parse_date(item.get("Published") or item.get("Created")),
                    department=categories[0] if categories else None,
                )
            )
        return jobs


register(HRManagerAdapter())
