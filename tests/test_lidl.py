"""Lidl's job site: the search API's records are whole adverts."""

from __future__ import annotations

import json

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.bespoke.lidl import LidlAdapter, to_raw_job


def _item(n: int, contract: str = "Part Time") -> dict:
    return {
        "requisitionId": str(750000 + n), "title": f"Customer Assistant - Town {n}",
        "location": {"city": "Thomastown", "country": "IE", "name": "Thomastown / Store IE0001"},
        "contractType": contract, "employmentArea": "Store",
        "descResponsibilities": "<p>Earn on our standard 20 hour contract.</p>",
        "onlineFrom": "2026-09-25T13:11:57+00:00",
        "jobDetailUrl": f"https://jobs.lidl.ie/jobs/customer-assistant-{n}",
    }


def test_a_record_keeps_its_town_contract_and_advert() -> None:
    job = to_raw_job(_item(1))
    assert job.location_raw == "Thomastown, Ireland"
    assert job.employment_type == "Part Time"
    assert job.url == "https://jobs.lidl.ie/jobs/customer-assistant-1"
    assert "20 hour contract" in job.description


@respx.mock
def test_the_search_is_read_at_one_hundred_a_page() -> None:
    route = respx.get("https://jobs.lidl.ie/api/v1/search").mock(return_value=httpx.Response(
        200, json={"jobs": [_item(1), _item(2, "Full Time")], "meta": {"totalCount": 2}}))
    result = LidlAdapter().fetch("jobs.lidl.ie")
    assert result.status is CrawlStatus.OK and len(result.jobs) == 2
    assert json.loads(route.calls[0].request.url.params["general"])["resultsPerPage"] == 100


@respx.mock
def test_fewer_records_than_the_total_is_a_sample() -> None:
    respx.get("https://jobs.lidl.ie/api/v1/search").mock(return_value=httpx.Response(
        200, json={"jobs": [_item(1)], "meta": {"totalCount": 9}}))
    assert LidlAdapter().fetch("jobs.lidl.ie").status is CrawlStatus.PARTIAL
