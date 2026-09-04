"""Adapter parsing tests, using recorded response shapes from the live APIs."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.ashby import AshbyAdapter
from jobfinder.sources.greenhouse import GreenhouseAdapter

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/testco/jobs"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/testco"


# A London role at a company whose office list also contains Dublin. This is the exact
# shape Intercom returns, and the reason `offices` is not treated as a per-job location.
GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 1,
            "title": "Account Executive, MidMarket",
            "absolute_url": "https://example.com/1",
            "location": {"name": "London, England"},
            "offices": [{"name": "Dublin, Ireland"}, {"name": "London, England"}],
            "content": "&lt;p&gt;Sell things&lt;/p&gt;",
            "first_published": "2026-01-15T10:00:00-05:00",
            "departments": [{"name": "Sales"}],
        },
        {
            "id": 2,
            "title": "Backend Engineer",
            "absolute_url": "https://example.com/2",
            "location": {"name": "Dublin, Ireland"},
            "offices": [{"name": "Dublin, Ireland"}, {"name": "London, England"}],
            "content": "&lt;p&gt;Build things&lt;/p&gt;",
            "first_published": "2026-02-01T10:00:00-05:00",
            "departments": [{"name": "Engineering"}],
        },
        {
            "id": 3,
            "title": "Roving Role",
            "absolute_url": "https://example.com/3",
            "location": {"name": ""},  # no location: offices are the best signal
            "offices": [{"name": "Dublin, Ireland"}],
            "content": "",
        },
    ]
}

ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "abc-1",
            "title": "Security Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/testco/abc-1",
            "location": "New York, NY (HQ)",
            "secondaryLocations": [{"location": "Dublin, Ireland"}],
            "descriptionPlain": "Secure things",
            "publishedAt": "2026-04-07T17:12:35.753+00:00",
            "department": "Engineering",
            "isListed": True,
        },
        {
            "id": "abc-2",
            "title": "Hidden Role",
            "jobUrl": "https://jobs.ashbyhq.com/testco/abc-2",
            "location": "Dublin, Ireland",
            "isListed": False,  # must be skipped
        },
    ]
}


@respx.mock
def test_greenhouse_parses_and_unescapes():
    respx.get(GREENHOUSE_URL).mock(
        return_value=httpx.Response(200, json=GREENHOUSE_PAYLOAD)
    )
    result = GreenhouseAdapter().fetch("testco")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 3

    job = result.jobs[0]
    assert job.description == "<p>Sell things</p>", "content must be HTML-unescaped"
    assert job.posted_at is not None
    assert job.department == "Sales"


@respx.mock
def test_greenhouse_company_offices_do_not_leak_into_job_location():
    """Regression: a London role at a company with a Dublin office is not a Dublin role.

    Greenhouse attaches the whole company office list to every posting, so treating
    `offices` as per-job locations marked every London job as Dublin.
    """
    respx.get(GREENHOUSE_URL).mock(
        return_value=httpx.Response(200, json=GREENHOUSE_PAYLOAD)
    )
    jobs = {j.title: j for j in GreenhouseAdapter().fetch("testco").jobs}

    assert jobs["Account Executive, MidMarket"].extra_locations == []
    assert jobs["Backend Engineer"].extra_locations == []
    # With no location of its own, the office list is the best signal available.
    assert jobs["Roving Role"].extra_locations == ["Dublin, Ireland"]


@respx.mock
def test_ashby_expands_secondary_locations():
    """Ashby's secondaryLocations really is per-posting, so it must be honoured."""
    respx.get(ASHBY_URL).mock(return_value=httpx.Response(200, json=ASHBY_PAYLOAD))
    result = AshbyAdapter().fetch("testco")

    assert len(result.jobs) == 1, "unlisted postings must be skipped"
    job = result.jobs[0]
    assert job.location_raw == "New York, NY (HQ)"
    assert job.extra_locations == ["Dublin, Ireland"]


@respx.mock
def test_http_error_becomes_failed_not_an_exception():
    respx.get(GREENHOUSE_URL).mock(return_value=httpx.Response(500))
    result = GreenhouseAdapter().fetch("testco")

    assert result.status is CrawlStatus.FAILED
    assert result.jobs == []
    assert "500" in (result.error or "")


@respx.mock
def test_malformed_json_becomes_failed():
    respx.get(GREENHOUSE_URL).mock(return_value=httpx.Response(200, text="not json"))
    assert GreenhouseAdapter().fetch("testco").status is CrawlStatus.FAILED


@respx.mock
def test_network_error_becomes_failed():
    respx.get(GREENHOUSE_URL).mock(side_effect=httpx.ConnectError("dns failure"))
    result = GreenhouseAdapter().fetch("testco")
    assert result.status is CrawlStatus.FAILED
