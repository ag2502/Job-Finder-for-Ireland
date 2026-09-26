"""Workday adapter tests, against a simulated tenant shaped like the live CXS API."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.pipeline.state import resolve_location
from jobfinder.sources import workday
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.workday import IRELAND_COUNTRY_ID, WorkdayAdapter, irish_facets

SLUG = "acme:wd3:Careers"
BASE = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers"
LONDON_ID = "london-office"
DUBLIN_ID = "dublin-office"


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def posting(n: int, city: str, locations_text: str | None = None) -> dict:
    return {
        "title": f"Role {n}",
        "externalPath": f"/job/{city}/Role-{n}_R-{n}",
        "locationsText": locations_text or f"{city}, Ireland",
        "postedOn": "Posted Today",
        "bulletFields": [f"R-{n}"],
    }


class Tenant:
    """A tenant that answers like Workday: `total` on the first page only."""

    def __init__(self, postings: list[dict], *, facets: list[dict], by_facet, by_text,
                 details: dict[str, dict]):
        self.postings = postings
        self.facets = facets
        self.by_facet = by_facet
        self.by_text = by_text
        self.details = details
        self.detail_requests: list[str] = []

    def jobs(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        applied, text = body["appliedFacets"], body["searchText"]
        if applied:
            matched = [p for p in self.postings if self.by_facet(p, applied)]
        elif text:
            matched = [p for p in self.postings if self.by_text(p, text)]
        else:
            matched = self.postings
        offset, limit = body["offset"], body["limit"]
        return httpx.Response(200, json={
            "total": len(matched) if offset == 0 else 0,
            "jobPostings": matched[offset:offset + limit],
            "facets": self.facets if offset == 0 else [],
        })

    def detail(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wday/cxs/acme/Careers")
        self.detail_requests.append(path)
        info = self.details.get(path, {"location": None})
        return httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "Do things", **info}})

    def mount(self) -> None:
        respx.post(f"{BASE}/jobs").mock(side_effect=self.jobs)
        respx.get(url__startswith=f"{BASE}/job/").mock(side_effect=self.detail)


@respx.mock
def test_every_page_is_read_although_total_is_only_on_the_first():
    """Regression: Mastercard's 60 Dublin roles were crawled as 37.

    Workday reports `total` on page one and 0 thereafter, and the loop re-read it on
    every page, so it stopped after twenty postings per query.
    """
    dublin = [posting(n, "Dublin") for n in range(45)]
    tenant = Tenant(
        dublin + [posting(100, "London", "London, England")],
        facets=[{"facetParameter": "locations", "values": [
            {"descriptor": "Dublin, Ireland", "id": DUBLIN_ID, "count": 45},
            {"descriptor": "London, England", "id": LONDON_ID, "count": 1},
        ]}],
        by_facet=lambda p, applied: DUBLIN_ID in applied.get("locations", [])
        and "Dublin" in p["externalPath"],
        by_text=lambda p, text: "Dublin" in p["externalPath"],
        details={},
    )
    tenant.mount()

    result = WorkdayAdapter().fetch(SLUG)

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 45
    assert {j.source_job_id for j in result.jobs} == {f"R-{n}" for n in range(45)}


@respx.mock
def test_a_role_led_from_another_office_is_kept_through_its_additional_locations():
    """The list names only the first office; `additionalLocations` names Dublin."""
    stockholm = posting(1, "Stockholm-Sweden", "6 Locations")
    london = posting(2, "London", "London, England")
    tenant = Tenant(
        [stockholm, london],
        facets=[{"facetParameter": "locations", "values": [
            {"descriptor": "London, England", "id": LONDON_ID, "count": 1},
        ]}],
        by_facet=lambda p, applied: False,
        # Both mention Dublin in their text; only one is actually open there.
        by_text=lambda p, text: True,
        details={
            stockholm["externalPath"]: {
                "location": "Stockholm, Sweden",
                "additionalLocations": ["London, England", "Dublin, Ireland"],
            },
            london["externalPath"]: {"location": "London, England"},
        },
    )
    tenant.mount()

    result = WorkdayAdapter().fetch(SLUG)

    assert [j.source_job_id for j in result.jobs] == ["R-1"]
    job = result.jobs[0]
    assert job.location_raw == "Stockholm, Sweden"
    assert "Dublin, Ireland" in job.extra_locations
    assert resolve_location(job).is_dublin
    # A plain London hit is judged from its list entry, without a detail request.
    assert london["externalPath"] not in tenant.detail_requests


@respx.mock
def test_the_country_facet_is_found_under_whatever_name_the_tenant_gave_it():
    remote = posting(1, "Ireland-Remote", "Ireland - Remote")
    cork = posting(2, "Cork", "Cork, Ireland")
    tenant = Tenant(
        [remote, cork, posting(3, "Paris", "Paris, France")],
        facets=[{"facetParameter": "Country_and_Jurisdiction", "values": [
            {"descriptor": "Ireland", "id": IRELAND_COUNTRY_ID, "count": 2},
            {"descriptor": "France", "id": "france", "count": 1},
        ]}],
        by_facet=lambda p, applied: IRELAND_COUNTRY_ID
        in applied.get("Country_and_Jurisdiction", [])
        and "Paris" not in p["externalPath"],
        by_text=lambda p, text: False,
        details={
            remote["externalPath"]: {"location": "Ireland - Remote"},
            cork["externalPath"]: {"location": "Cork, Ireland"},
        },
    )
    tenant.mount()

    result = WorkdayAdapter().fetch(SLUG)

    # Every Irish role comes back; the pipeline, not the adapter, decides what is Dublin.
    assert sorted(j.location_raw for j in result.jobs) == ["Cork, Ireland", "Ireland - Remote"]
    remote_job = next(j for j in result.jobs if j.source_job_id == "R-1")
    assert resolve_location(remote_job).is_remote


def test_facets_select_irish_offices_but_not_a_us_dublin():
    facets = [
        {"facetParameter": "locationMainGroup", "values": [
            {"facetParameter": "locations", "values": [
                {"descriptor": "Dublin, Ireland (One South County)", "id": "a"},
                {"descriptor": "Dublin, CA", "id": "b"},
                {"descriptor": "Pune, India", "id": "c"},
            ]},
        ]},
        {"facetParameter": "Location_Country", "values": [
            {"descriptor": "Ireland", "id": IRELAND_COUNTRY_ID},
        ]},
        # A job family called "Ireland" is not a location and must not be applied.
        {"facetParameter": "jobFamilyGroup", "values": [
            {"descriptor": "Ireland Sales", "id": "d"},
        ]},
    ]

    assert irish_facets(facets) == [
        {"Location_Country": [IRELAND_COUNTRY_ID]},
        {"locations": ["a"]},
    ]


@respx.mock
def test_a_query_cut_off_by_the_page_ceiling_is_partial(monkeypatch):
    """A truncated read must not close the roles it never reached."""
    monkeypatch.setattr(workday, "MAX_PAGES", 2)
    tenant = Tenant(
        [posting(n, "Dublin") for n in range(60)],
        facets=[],
        by_facet=lambda p, applied: False,
        by_text=lambda p, text: True,
        details={},
    )
    tenant.mount()

    result = WorkdayAdapter().fetch(SLUG)

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == 40


@respx.mock
def test_a_tag_in_the_first_bullet_is_not_used_as_the_id():
    """Intel's first bullet is sometimes "Spotlight Job", shared by many roles."""
    first = {**posting(1, "Dublin"), "bulletFields": ["Spotlight Job"]}
    second = {**posting(2, "Dublin"), "bulletFields": ["Spotlight Job"]}
    tenant = Tenant(
        [first, second, posting(3, "Dublin")],
        facets=[{"facetParameter": "locations", "values": [
            {"descriptor": "Dublin, Ireland", "id": DUBLIN_ID},
        ]}],
        by_facet=lambda p, applied: True,
        by_text=lambda p, text: False,
        details={
            first["externalPath"]: {"location": "Dublin, Ireland", "jobReqId": "JR0001"},
            second["externalPath"]: {"location": "Dublin, Ireland", "jobReqId": "JR0002"},
        },
    )
    tenant.mount()

    result = WorkdayAdapter().fetch(SLUG)

    # Real requisition ids keep the ids stored for existing roles unchanged.
    assert sorted(j.source_job_id for j in result.jobs) == ["JR0001", "JR0002", "R-3"]
