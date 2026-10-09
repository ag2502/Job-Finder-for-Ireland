"""JobAlert.ie: ten jobs per server-rendered page, read from the page data."""

from __future__ import annotations

import json

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.boards import jobalert
from jobfinder.sources.boards.jobalert import JobAlertAdapter, page_jobs, to_raw_job


def _item(n: int, *, title="Sales Assistant", company="SuperValu", formatted="Ballinrobe, County Mayo",
          country_code="ie", types=("Part-time",), status="OPEN") -> dict:
    return {
        "_id": f"id{n}", "slug": f"job-{n}", "title": title, "status": status, "isOpen": status == "OPEN",
        "company": {"name": company},
        "address": {"formatted": formatted, "county": "Berkshire", "countryCode": country_code,
                    "country": "Ireland" if country_code == "ie" else "United Kingdom"},
        "description": "<p>Part time hours available.</p>",
        "jobTypes": [{"name": t} for t in types],
        "postedAt": "2026-10-01T12:40:02.040Z",
    }


def _page(items: list[dict], count: int) -> str:
    data = {"props": {"pageProps": {"initialReduxState": {"entities": {"jobs": {
        "data": items, "count": count, "pageSize": 10}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_a_job_keeps_its_employer_place_and_hours() -> None:
    job = to_raw_job(_item(1, types=("Full-time", "Part-time")))
    assert job.company_name == "SuperValu"
    assert job.location_raw == "Ballinrobe, County Mayo, Ireland"
    assert job.employment_type == "Full-time, Part-time"
    assert job.url == "https://www.jobalert.ie/job/job-1"


def test_places_are_read_from_the_country_code_not_the_geocoded_county() -> None:
    # The board's geocoder puts Bray in Berkshire.
    assert to_raw_job(_item(1, formatted="Bray")).location_raw == "Bray, Ireland"
    assert to_raw_job(_item(1, formatted="Nationwide")).location_raw == "Ireland"
    assert to_raw_job(_item(1, formatted="Belfast", country_code="gb")).location_raw == "Belfast, United Kingdom"
    assert to_raw_job(_item(1, status="CLOSED")) is None


@respx.mock
def test_the_board_is_read_until_a_page_comes_back_empty(monkeypatch) -> None:
    monkeypatch.setattr(JobAlertAdapter, "polite_pause", staticmethod(lambda: None))
    pages = {1: [_item(1), _item(2)], 2: [_item(3)], 3: []}
    respx.get(url__startswith=jobalert.ORIGIN + "/jobs").mock(
        side_effect=lambda request: httpx.Response(
            200, text=_page(pages[int(request.url.params["page"])], 3)))

    result = JobAlertAdapter().fetch("jobalert.ie")
    assert result.status is CrawlStatus.OK
    assert [j.source_job_id for j in result.jobs] == ["id1", "id2", "id3"]


@respx.mock
def test_a_read_far_short_of_the_board_count_is_a_sample(monkeypatch) -> None:
    monkeypatch.setattr(JobAlertAdapter, "polite_pause", staticmethod(lambda: None))
    respx.get(url__startswith=jobalert.ORIGIN + "/jobs").mock(
        side_effect=lambda request: httpx.Response(
            200, text=_page([_item(1)] if request.url.params["page"] == "1" else [], 50)))
    assert JobAlertAdapter().fetch("jobalert.ie").status is CrawlStatus.PARTIAL


def test_a_page_without_page_data_is_an_error() -> None:
    try:
        page_jobs("<html></html>")
    except ValueError:
        return
    raise AssertionError("expected a ValueError")
