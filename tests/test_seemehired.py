"""SeeMeHired: the platform's public search, Irish jobs kept, filed under each employer."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.boards.seemehired import SEARCH, SeeMeHiredAdapter, to_raw_job


def _item(n, country="Ireland", town="Naas", contract="Part time", employer="Caremark Kildare"):
    return {"id": n, "title": f"Healthcare Assistant {n}", "employer": employer, "isActive": True,
            "isJobInternal": False, "publishedAt": 1791529200, "contractType": {"name": contract},
            "location": {"cityName": town, "countryName": country, "fullAddress": f"{town}, {country}"}}


def test_only_republic_jobs_are_kept_under_their_employer():
    job = to_raw_job(_item(1))
    assert (job.company_name, job.location_raw, job.employment_type) == ("Caremark Kildare", "Naas, Ireland", "Part time")
    assert job.url == "https://seemehired.com/jobs/1"
    assert to_raw_job(_item(2, country="Northern Ireland", town="Belfast")) is None
    assert to_raw_job(_item(3, country="England", town="Leeds")) is None
    # A town given as the country itself falls back to the full address.
    assert to_raw_job(_item(4, town="Ireland")).location_raw == "Ireland, Ireland"


@respx.mock
def test_the_whole_board_is_paged_through(monkeypatch):
    monkeypatch.setattr(SeeMeHiredAdapter, "polite_pause", staticmethod(lambda: None))
    pages = {"1": [_item(1), _item(2, country="Wales")], "2": [_item(3)]}
    respx.get(SEARCH).mock(side_effect=lambda request: httpx.Response(
        200, json={"items": pages[request.url.params["page"]], "total": 3}))
    result = SeeMeHiredAdapter().fetch("all")
    assert result.status is CrawlStatus.OK
    assert [j.source_job_id for j in result.jobs] == ["1", "3"]
