"""Adapter parsing tests, using recorded response shapes from the live APIs."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.ashby import AshbyAdapter
from jobfinder.sources.greenhouse import GreenhouseAdapter
from jobfinder.sources.personio import PersonioAdapter
from jobfinder.sources.recruitee import RecruiteeAdapter
from jobfinder.sources.smartrecruiters import SmartRecruitersAdapter, verify_slug

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


# ---------------------------------------------------------------------------
# SmartRecruiters
# ---------------------------------------------------------------------------

SR_ROOT = "https://api.smartrecruiters.com/v1/companies"

SR_LIST = {
    "offset": 0,
    "limit": 100,
    "totalFound": 1,
    "content": [
        {
            "id": "744000147691469",
            "name": "Lead Front-end Developer",
            "releasedDate": "2026-09-06T14:53:03.696Z",
            # `fullLocation` is deliberately malformed here, exactly as the live API
            # returns it, to prove the adapter rebuilds from the structured parts.
            "location": {
                "city": "Dublin",
                "region": "Leinster",
                "country": "ie",
                "fullLocation": "Dublin, , Ireland",
            },
            "department": {"label": "Engineering"},
        }
    ],
}

SR_DETAIL = {
    **SR_LIST["content"][0],
    "postingUrl": "https://jobs.smartrecruiters.com/TestCo/744000147691469",
    "jobAd": {
        "sections": {
            "companyDescription": {"text": "<p>About us</p>"},
            "jobDescription": {"text": "<p>Build things</p>"},
            "qualifications": {"text": "<p>Python</p>"},
            "videos": {"urls": []},
        }
    },
}


@respx.mock
def test_smartrecruiters_assembles_ad_sections_in_reading_order():
    """The role must lead the indexed text; employer boilerplate comes last."""
    respx.get(f"{SR_ROOT}/TestCo/postings").mock(
        return_value=httpx.Response(200, json=SR_LIST)
    )
    respx.get(f"{SR_ROOT}/TestCo/postings/744000147691469").mock(
        return_value=httpx.Response(200, json=SR_DETAIL)
    )
    result = SmartRecruitersAdapter().fetch("TestCo")

    assert result.status is CrawlStatus.OK
    job = result.jobs[0]
    assert job.description == "<p>Build things</p>\n\n<p>Python</p>\n\n<p>About us</p>"
    assert job.location_raw == "Dublin, Leinster, IE", "malformed fullLocation is unused"
    assert job.url == "https://jobs.smartrecruiters.com/TestCo/744000147691469"
    assert job.department == "Engineering"


@respx.mock
def test_smartrecruiters_detail_failure_keeps_the_posting():
    """Losing a description costs ranking quality; losing the posting loses the job."""
    respx.get(f"{SR_ROOT}/TestCo/postings").mock(
        return_value=httpx.Response(200, json=SR_LIST)
    )
    respx.get(f"{SR_ROOT}/TestCo/postings/744000147691469").mock(
        return_value=httpx.Response(500)
    )
    result = SmartRecruitersAdapter().fetch("TestCo")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 1
    assert result.jobs[0].description is None
    assert result.jobs[0].title == "Lead Front-end Developer"


@respx.mock
def test_smartrecruiters_unknown_tenant_is_caught_at_registration():
    """A typo'd slug answers 200 with an empty list, not 404.

    The crawl cannot tell that apart from a company that has closed its last vacancy,
    so the check has to happen once, at registration.
    """
    empty = {"offset": 0, "limit": 1, "totalFound": 0, "content": []}
    respx.get(f"{SR_ROOT}/Typo/postings").mock(
        return_value=httpx.Response(200, json=empty)
    )
    respx.get(f"{SR_ROOT}/Real/postings").mock(
        return_value=httpx.Response(200, json={**empty, "totalFound": 412})
    )

    assert verify_slug("Typo") is False
    assert verify_slug("Real") is True


# ---------------------------------------------------------------------------
# Recruitee
# ---------------------------------------------------------------------------

RECRUITEE_URL = "https://testco.recruitee.com/api/offers/"

RECRUITEE_PAYLOAD = {
    "offers": [
        {
            "id": 2727516,
            "title": "Product Designer",
            "slug": "product-designer",
            "status": "published",
            "location": "Dublin, Leinster, Ireland",
            "locations": [
                {"city": "Dublin", "state": "Leinster", "country": "Ireland"},
                {"city": "Cork", "country": "Ireland"},
            ],
            "description": "<p>Design things</p>",
            "requirements": "<p>Figma</p>",
            "published_at": "2026-08-31 12:04:10 UTC",
            "department": "Product",
            "careers_url": "https://testco.recruitee.com/o/product-designer",
        },
        {
            "id": 2727517,
            "title": "Draft Role",
            "status": "draft",  # must be skipped
            "location": "Dublin, Ireland",
        },
    ]
}


@respx.mock
def test_recruitee_expands_locations_and_joins_requirements():
    respx.get(RECRUITEE_URL).mock(
        return_value=httpx.Response(200, json=RECRUITEE_PAYLOAD)
    )
    result = RecruiteeAdapter().fetch("testco")

    assert len(result.jobs) == 1, "unpublished offers must be skipped"
    job = result.jobs[0]
    # The primary is already formatted, so only the genuinely different second office
    # survives de-duplication.
    assert job.extra_locations == ["Cork, Ireland"]
    assert job.description == "<p>Design things</p>\n\n<p>Figma</p>"
    assert job.department == "Product"


@respx.mock
def test_recruitee_unknown_tenant_fails_rather_than_reporting_an_empty_board():
    """404 must reach the reconciler as FAILED so nothing gets closed."""
    respx.get(RECRUITEE_URL).mock(
        return_value=httpx.Response(404, json={"error": "Not Found"})
    )
    result = RecruiteeAdapter().fetch("testco")

    assert result.status is CrawlStatus.FAILED
    assert result.jobs == []


# ---------------------------------------------------------------------------
# Personio
# ---------------------------------------------------------------------------

PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>1822270</id>
    <office>Dublin</office>
    <offices>
      <office>Dublin</office>
      <office>Cork</office>
    </offices>
    <recruitingCategory>Engineering</recruitingCategory>
    <name>Junior Security Consultant</name>
    <jobDescriptions>
      <jobDescription>
        <name>Your responsibilities</name>
        <value><![CDATA[<p>Audit things</p>]]></value>
      </jobDescription>
      <jobDescription>
        <name>Your profile</name>
        <value><![CDATA[<p>Python, Go</p>]]></value>
      </jobDescription>
    </jobDescriptions>
    <createdAt>2024-09-03T10:21:21+00:00</createdAt>
  </position>
</workzag-jobs>
"""


@respx.mock
def test_personio_concatenates_every_description_block():
    """The requirements block carries most of the matchable vocabulary."""
    respx.get("https://testco.jobs.personio.de/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    result = PersonioAdapter().fetch("testco")

    job = result.jobs[0]
    assert "Audit things" in (job.description or "")
    assert "Python, Go" in (job.description or ""), "second block must not be dropped"
    assert job.location_raw == "Dublin"
    assert job.extra_locations == ["Cork"]
    assert job.url == "https://testco.jobs.personio.de/job/1822270"
    assert job.department == "Engineering"


@respx.mock
def test_personio_falls_back_from_de_to_com():
    """A tenant's host is not derivable from its slug, so .de 404 means 'try .com'."""
    respx.get("https://testco.jobs.personio.de/xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://testco.jobs.personio.com/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    result = PersonioAdapter().fetch("testco")

    assert result.status is CrawlStatus.OK
    assert result.jobs[0].url.startswith("https://testco.jobs.personio.com/")


@respx.mock
def test_personio_pinned_host_skips_the_wasted_request():
    route = respx.get("https://testco.jobs.personio.com/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    de = respx.get("https://testco.jobs.personio.de/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    assert PersonioAdapter().fetch("testco:com").status is CrawlStatus.OK
    assert route.called and not de.called


@respx.mock
def test_personio_server_error_is_not_mistaken_for_a_missing_tenant():
    """Only 404 means 'wrong host'. A 500 must stay a failure."""
    respx.get("https://testco.jobs.personio.de/xml").mock(
        return_value=httpx.Response(500)
    )
    com = respx.get("https://testco.jobs.personio.com/xml").mock(
        return_value=httpx.Response(200, text=PERSONIO_XML)
    )
    result = PersonioAdapter().fetch("testco")

    assert result.status is CrawlStatus.FAILED
    assert not com.called, "a 500 must not fall through to the other host"
