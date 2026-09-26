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


# ---------------------------------------------------------------------------
# BambooHR, Teamtailor, iCIMS, Oracle Recruiting, SuccessFactors
# ---------------------------------------------------------------------------


@respx.mock
def test_bamboohr_reads_the_list_and_builds_locations():
    from jobfinder.sources.bamboohr import BambooHRAdapter

    respx.get("https://acme.bamboohr.com/careers/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"totalCount": 2},
                "result": [
                    {"id": "445", "jobOpeningName": "Security Consultant",
                     "departmentLabel": "Services", "location": {"city": "Dublin", "state": None},
                     "atsLocation": {"country": "Ireland", "state": None, "province": None, "city": None},
                     "isRemote": None},
                    {"id": "446", "jobOpeningName": "Support Engineer",
                     "location": {"city": None, "state": None}, "atsLocation": {}, "isRemote": True},
                ],
            },
        )
    )

    result = BambooHRAdapter().fetch("acme")

    assert result.status is CrawlStatus.OK
    first, second = result.jobs
    assert (first.source_job_id, first.location_raw) == ("445", "Dublin, Ireland")
    assert first.url == "https://acme.bamboohr.com/careers/445"
    assert first.department == "Services"
    assert second.location_raw == "Remote"


@respx.mock
def test_bamboohr_unknown_tenant_fails_rather_than_emptying_the_board():
    """An unknown tenant is redirected to BambooHR's marketing HTML, not a 404."""
    from jobfinder.sources.bamboohr import BambooHRAdapter

    respx.get("https://nobody.bamboohr.com/careers/list").mock(
        return_value=httpx.Response(200, text="<html>Try BambooHR free</html>")
    )
    assert BambooHRAdapter().fetch("nobody").status is CrawlStatus.FAILED


TEAMTAILOR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/rss">
  <channel>
    <item>
      <title>Director, Customer Operations</title>
      <description>&lt;p&gt;Lead support&lt;/p&gt;</description>
      <pubDate>Wed, 02 Sep 2026 05:53:00 +0100</pubDate>
      <link>https://careers.acme.ie/jobs/8306207-director-customer-operations</link>
      <remoteStatus>none</remoteStatus>
      <guid>972fcd39</guid>
      <tt:department>Operations</tt:department>
      <tt:locations>
        <tt:location><tt:name>HQ</tt:name><tt:city>Dublin</tt:city><tt:country>Ireland</tt:country></tt:location>
        <tt:location><tt:name>US</tt:name><tt:city>Philadelphia</tt:city><tt:country>United States</tt:country></tt:location>
      </tt:locations>
    </item>
    <item>
      <title>Remote Engineer</title>
      <link>https://careers.acme.ie/jobs/8306208-remote-engineer</link>
      <remoteStatus>fully</remoteStatus>
      <guid>972fcd40</guid>
    </item>
  </channel>
</rss>"""


@respx.mock
def test_teamtailor_expands_namespaced_locations_and_marks_remote():
    from jobfinder.sources.teamtailor import TeamtailorAdapter

    respx.get("https://careers.acme.ie/jobs.rss").mock(
        return_value=httpx.Response(200, text=TEAMTAILOR_FEED)
    )

    result = TeamtailorAdapter().fetch("careers.acme.ie")

    assert result.status is CrawlStatus.OK
    director, remote = result.jobs
    assert director.source_job_id == "8306207"
    assert director.location_raw == "Dublin, Ireland"
    assert director.extra_locations == ["Philadelphia, United States"]
    assert director.department == "Operations"
    assert remote.location_raw == "Remote"


def test_teamtailor_bare_subdomain_and_custom_host_both_address_a_feed():
    from jobfinder.sources.teamtailor import feed_url

    assert feed_url("acme") == "https://acme.teamtailor.com/jobs.rss"
    assert feed_url("careers.acme.ie") == "https://careers.acme.ie/jobs.rss"


def _icims_page(ids: list[int], total: int) -> dict:
    return {
        "totalCount": total,
        "jobs": [
            {"data": {"req_id": str(i), "slug": str(i), "title": f"Role {i}",
                      "full_location": "Dublin, Ireland", "description": "Do things",
                      "qualifications": "Know things", "posted_date": "2026-09-11T13:14:00+0000"}}
            for i in ids
        ],
    }


@respx.mock
def test_icims_resolves_a_bare_portal_and_pages_to_the_stated_total():
    from jobfinder.sources.icims import ICIMSAdapter

    respx.get("https://careers-acme.icims.com/jobs/search?ss=1").mock(
        return_value=httpx.Response(
            200, text="<script>window.top.location.href = 'https:\\/\\/careers.acme.ie\\/jobs';</script>"
        )
    )
    respx.get("https://careers.acme.ie/api/jobs", params={"page": "1"}).mock(
        return_value=httpx.Response(200, json=_icims_page([1, 2], total=3))
    )
    respx.get("https://careers.acme.ie/api/jobs", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=_icims_page([3], total=3))
    )

    result = ICIMSAdapter().fetch("careers-acme")

    assert result.status is CrawlStatus.OK
    assert [job.source_job_id for job in result.jobs] == ["1", "2", "3"]
    assert result.jobs[0].url == "https://careers.acme.ie/jobs/1"
    assert "Know things" in result.jobs[0].description


@respx.mock
def test_icims_incomplete_read_fails_instead_of_closing_unread_jobs():
    """A board that stops paging far short of its own total is not a complete board."""
    from jobfinder.sources.icims import ICIMSAdapter

    respx.get("https://careers.acme.ie/api/jobs", params={"page": "1"}).mock(
        return_value=httpx.Response(200, json=_icims_page([1, 2], total=50))
    )
    respx.get("https://careers.acme.ie/api/jobs", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=_icims_page([], total=50))
    )

    result = ICIMSAdapter().fetch("careers.acme.ie")

    assert result.status is CrawlStatus.FAILED
    assert "incomplete" in (result.error or "")


ORACLE_ENDPOINT = "https://jobs.acme.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"


def _oracle_page(ids: list[str], total: int) -> dict:
    return {
        "items": [
            {
                "TotalJobsCount": total,
                "requisitionList": [
                    {"Id": i, "Title": f"Engineer {i}", "PostedDate": "2026-09-11",
                     "PrimaryLocation": "Dublin, Ireland",
                     "secondaryLocations": [{"Name": "Cork, Ireland"}]}
                    for i in ids
                ],
            }
        ]
    }


@respx.mock
def test_oracle_recruiting_reads_requisitions_with_their_site_urls():
    from jobfinder.sources.oracle_recruiting import OracleRecruitingAdapter

    respx.get(url__startswith=ORACLE_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_oracle_page(["R1", "R2"], total=2))
    )

    result = OracleRecruitingAdapter().fetch("jobs.acme.com|CX_1")

    assert result.status is CrawlStatus.OK
    first = result.jobs[0]
    assert first.source_job_id == "R1"
    assert first.location_raw == "Dublin, Ireland"
    assert first.extra_locations == ["Cork, Ireland"]
    assert first.url == "https://jobs.acme.com/hcmUI/CandidateExperience/en/sites/CX_1/job/R1"


@respx.mock
def test_oracle_recruiting_reads_one_country_not_the_global_board():
    """Regression: JPMorgan's 7,495-role board overran the page ceiling every run."""
    from jobfinder.sources.oracle_recruiting import OracleRecruitingAdapter

    route = respx.get(url__startswith=ORACLE_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_oracle_page(["R1"], total=1))
    )

    OracleRecruitingAdapter().fetch("jobs.acme.com|CX_1")
    assert ",location=Ireland," in str(route.calls.last.request.url)

    OracleRecruitingAdapter().fetch("jobs.acme.com|CX_1|*")
    assert "location=" not in str(route.calls.last.request.url)


def test_oracle_recruiting_rejects_a_slug_without_its_site_number():
    """A pod name alone - what the old fingerprint captured - addresses nothing."""
    from jobfinder.sources.oracle_recruiting import OracleRecruitingAdapter

    assert OracleRecruitingAdapter().fetch("jobs.acme.com").status is CrawlStatus.FAILED


def test_oracle_slug_is_read_from_a_candidate_experience_url():
    from jobfinder.sources.oracle_recruiting import slug_from_url

    url = "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/jobs"
    assert slug_from_url(url) == "enterpriseplatform.dell.com|careers"


@respx.mock
def test_icims_reads_one_country_not_the_global_board():
    """AXA's board has 1,523 roles at ten a page, past the ceiling; Ireland has 17."""
    from jobfinder.sources.icims import ICIMSAdapter

    route = respx.get("https://careers.acme.ie/api/jobs").mock(
        return_value=httpx.Response(200, json=_icims_page([1], total=1))
    )

    ICIMSAdapter().fetch("careers.acme.ie")
    assert route.calls.last.request.url.params["country"] == "Ireland"

    ICIMSAdapter().fetch("careers.acme.ie|*")
    assert "country" not in route.calls.last.request.url.params


def _rmk_page(ids: list[int], total: int) -> str:
    rows = "".join(
        f'<tr class="data-row"><td class="colTitle">'
        f'<span class="jobTitle hidden-phone"><a href="/job/Dublin-Analyst/{i}/" class="jobTitle-link">'
        f"Analyst &amp; Planner {i}</a></span>"
        f'<span class="jobTitle visible-phone"><a class="jobTitle-link" href="/job/Dublin-Analyst/{i}/">'
        f"Analyst &amp; Planner {i}</a></span></td>"
        f'<td class="colLocation"><span class="jobLocation"> Dublin, IE </span></td></tr>'
        for i in ids
    )
    return f'<span class="paginationLabel">Results <b>1 – 2</b> of <b>{total}</b></span><table><tbody>{rows}</tbody></table>'


@respx.mock
def test_successfactors_pages_the_search_results_and_dedupes_phone_links():
    from jobfinder.sources.successfactors import SuccessFactorsAdapter

    pages = {"0": _rmk_page([101, 102], total=3), "2": _rmk_page([103], total=3)}
    respx.get(url__startswith="https://careers.acme.ie/search/").mock(
        side_effect=lambda request: httpx.Response(200, text=pages[request.url.params["startrow"]])
    )

    result = SuccessFactorsAdapter().fetch("careers.acme.ie")

    assert result.status is CrawlStatus.OK
    assert [job.source_job_id for job in result.jobs] == ["101", "102", "103"]
    assert result.jobs[0].title == "Analyst & Planner 101"
    assert result.jobs[0].location_raw == "Dublin, IE"
    assert result.jobs[0].url == "https://careers.acme.ie/job/Dublin-Analyst/101/"


@respx.mock
def test_successfactors_board_beyond_the_page_ceiling_is_partial(monkeypatch):
    """SAP-sized boards are read newest-first to the ceiling and must not close the rest."""
    from jobfinder.sources import successfactors

    monkeypatch.setattr(successfactors, "MAX_PAGES", 2)
    counter = iter(range(1000, 2000, 2))
    respx.get(url__startswith="https://jobs.acme.com/search/").mock(
        side_effect=lambda request: httpx.Response(
            200, text=_rmk_page([next(counter), next(counter)], total=5000)
        )
    )

    result = successfactors.SuccessFactorsAdapter().fetch("jobs.acme.com")

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == 4


@respx.mock
def test_successfactors_global_board_is_searched_for_one_country():
    """SAP's board is global; `host|Ireland` makes it the site's own location search."""
    from jobfinder.sources.successfactors import SuccessFactorsAdapter

    route = respx.get(url__startswith="https://careers.acme.com/search/").mock(
        return_value=httpx.Response(200, text=_rmk_page([101], total=1))
    )

    assert SuccessFactorsAdapter().fetch("careers.acme.com|Ireland").status is CrawlStatus.OK
    assert route.calls.last.request.url.params["locationsearch"] == "Ireland"
    assert route.calls.last.request.url.host == "careers.acme.com"

    SuccessFactorsAdapter().fetch("careers.acme.com")
    assert "locationsearch" not in route.calls.last.request.url.params


@respx.mock
def test_successfactors_tenant_on_the_json_search_app_is_read_through_it():
    """CRH's search page renders no rows; its results come from a JSON endpoint."""
    import json as _json

    from jobfinder.sources.successfactors import SuccessFactorsAdapter

    respx.get(url__startswith="https://jobs.acme.com/search/").mock(
        return_value=httpx.Response(200, text="<div id='rmk-jobs-search'></div>")
    )

    def serve(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        assert body["location"] == "Ireland"
        ids = list(range(12))[body["pageNumber"] * 10:body["pageNumber"] * 10 + 10]
        return httpx.Response(200, json={"totalJobs": 12, "jobSearchResult": [
            {"response": {"id": str(i), "unifiedStandardTitle": f"Engineer {i}",
                          "unifiedUrlTitle": f"Engineer-{i}",
                          "jobLocationShort": ["Dublin, Leinster, Ireland ", "Cork, Munster, Ireland"]}}
            for i in ids
        ]})

    respx.post("https://jobs.acme.com/services/recruiting/v1/jobs").mock(side_effect=serve)

    result = SuccessFactorsAdapter().fetch("jobs.acme.com|Ireland")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 12
    job = result.jobs[0]
    assert (job.location_raw, job.extra_locations) == ("Dublin, Leinster, Ireland", ["Cork, Munster, Ireland"])
    assert job.url == "https://jobs.acme.com/job/Engineer-0/0-en_US/"


@respx.mock
def test_successfactors_classic_tenant_with_no_rows_still_fails():
    from jobfinder.sources.successfactors import SuccessFactorsAdapter

    respx.get(url__startswith="https://careers.acme.ie/search/").mock(
        return_value=httpx.Response(200, text="<table></table>")
    )
    respx.post("https://careers.acme.ie/services/recruiting/v1/jobs").mock(
        return_value=httpx.Response(401, json={"totalJobs": 0})
    )

    assert SuccessFactorsAdapter().fetch("careers.acme.ie").status is CrawlStatus.FAILED


@respx.mock
def test_successfactors_read_that_breaks_off_early_fails():
    from jobfinder.sources.successfactors import SuccessFactorsAdapter

    pages = {"0": _rmk_page([101, 102], total=40), "2": "<table><tbody></tbody></table>"}
    respx.get(url__startswith="https://careers.acme.ie/search/").mock(
        side_effect=lambda request: httpx.Response(200, text=pages[request.url.params["startrow"]])
    )

    assert SuccessFactorsAdapter().fetch("careers.acme.ie").status is CrawlStatus.FAILED


# ---------------------------------------------------------------------------
# CandidateManager and Oleeo (TAL.net)
# ---------------------------------------------------------------------------

CM_URL = "https://www.candidatemanager.net/cm/p/pJobs.aspx"


def _cm_page(headers: list[str], rows: list[list[str]], extra: str = "") -> str:
    head = "".join(f"<th>{h} </th>" for h in headers)
    body = "".join(
        "<tr><td><a href=\"https://www.candidatemanager.net/cm/p/pJobDetails.aspx?mid=M&amp;sid=S&amp;"
        f"jid={cells[0]}&amp;a=x\"> {cells[1]} </a><small>(REF{cells[0]})</small></td>"
        + "".join(f"<td> {c} </td>" for c in cells[2:])
        + "</tr>"
        for cells in rows
    )
    return f"<table class=\"table\"><tr>{head}</tr>{body}</table>{extra}"


@respx.mock
def test_candidatemanager_maps_columns_by_header_not_position():
    """Boards pick their own columns; EirGrid and RTÉ order them differently."""
    from jobfinder.sources.candidatemanager import CandidateManagerAdapter

    page = _cm_page(
        ["Current Vacancies", "Job Type", "Location", "Category"],
        [["J1", "Head of Outage Management", "Full-Time", "Dublin City, County Dublin, Ireland", "Engineering"]],
    )
    respx.get(CM_URL, params={"mid": "M", "sid": "S"}).mock(return_value=httpx.Response(200, text=page))

    result = CandidateManagerAdapter().fetch("M|S")

    assert result.status is CrawlStatus.OK
    (job,) = result.jobs
    assert job.source_job_id == "J1"
    assert job.location_raw == "Dublin City, County Dublin, Ireland"
    assert job.department == "Engineering"
    assert "jid=J1" in job.url and "&amp;" not in job.url


@respx.mock
def test_candidatemanager_board_without_a_location_column_reports_none():
    from jobfinder.sources.candidatemanager import CandidateManagerAdapter

    page = _cm_page(["Current Vacancies", "Job Type"], [["J2", "Associate Supervisor", "Permanent"]])
    respx.get(CM_URL, params={"mid": "M", "sid": "S"}).mock(return_value=httpx.Response(200, text=page))

    (job,) = CandidateManagerAdapter().fetch("M|S").jobs
    assert job.location_raw is None


@respx.mock
def test_candidatemanager_retired_board_fails_instead_of_closing_everything():
    from jobfinder.sources.candidatemanager import CandidateManagerAdapter

    respx.get(CM_URL, params={"mid": "M", "sid": "S"}).mock(
        return_value=httpx.Response(200, text="<html><body>Session expired</body></html>")
    )
    assert CandidateManagerAdapter().fetch("M|S").status is CrawlStatus.FAILED


def test_candidatemanager_slug_is_read_from_a_job_link():
    from jobfinder.sources.candidatemanager import slug_from_url

    url = "https://www.candidatemanager.net/cm/p/pJobDetails.aspx?mid=YGTAZW&amp;sid=BEVDEV&amp;jid=X"
    assert slug_from_url(url) == "YGTAZW|BEVDEV"


OLEEO_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://acme.tal.net/vx/candidate/so/pm/1/pl/3/opp/12954-Supply-Chain-Planner/en-GB</id>
    <link rel="alternate" href="https://acme.tal.net/vx/candidate/so/pm/1/pl/3/opp/12954-Supply-Chain-Planner/en-GB?instant=apply"/>
    <title>Supply Chain Planner</title>
    <published>2026-09-11T15:50:00Z</published>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">ID:12954<br/>
      Closing Date:30 Oct 2026 23:55 GMT<br/>County:Dublin<br/>Employment Type:Full Time<br/>Role Type:Head Office<br/></div></content>
  </entry>
  <entry>
    <id>https://acme.tal.net/vx/candidate/so/pm/1/pl/3/opp/12949-Cleaning-Assistant/en-GB</id>
    <title>Cleaning Assistant - Ballincollig</title>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">County:Cork<br/></div></content>
  </entry>
</feed>"""


@respx.mock
def test_oleeo_reads_county_and_fields_from_the_atom_feed():
    from jobfinder.sources.oleeo import OleeoAdapter, feed_url

    respx.get(feed_url("acme.tal.net|3")).mock(return_value=httpx.Response(200, text=OLEEO_FEED))

    result = OleeoAdapter().fetch("acme.tal.net|3")

    assert result.status is CrawlStatus.OK
    planner, cleaner = result.jobs
    assert (planner.source_job_id, planner.location_raw, planner.department) == ("12954", "Dublin", "Head Office")
    assert planner.url.endswith("/en-GB")
    assert "Closing Date: 30 Oct 2026" in planner.description
    assert (cleaner.source_job_id, cleaner.location_raw) == ("12949", "Cork")


@respx.mock
def test_oleeo_non_feed_response_fails():
    from jobfinder.sources.oleeo import OleeoAdapter, feed_url

    respx.get(feed_url("acme.tal.net|3")).mock(return_value=httpx.Response(200, text="<html>Maintenance</html>"))
    assert OleeoAdapter().fetch("acme.tal.net|3").status is CrawlStatus.FAILED


def test_oleeo_slug_is_read_from_a_board_url():
    from jobfinder.sources.oleeo import slug_from_url

    url = "https://dunnes.tal.net/vx/lang-en-GB/mobile-0/appcentre-ext/brand-4/candidate/jobboard/vacancy/3/adv/"
    assert slug_from_url(url) == "dunnes.tal.net|3"


# ---------------------------------------------------------------------------
# Phenom
# ---------------------------------------------------------------------------

PHENOM_HOME = '<script>phApp.ddo = {"siteConfig":{"locale":"en_gb","country":"gb","refNum":"ACMEGB","siteType":"external"}};</script>'


def _phenom_search(request: httpx.Request) -> httpx.Response:
    import json as _json

    body = _json.loads(request.content)
    if body["ddoKey"] == "jobDetail":
        return httpx.Response(200, json={"jobDetail": {"data": {"job": {"description": f"<p>Full advert {body['jobId']}</p>"}}}})
    assert (body["lang"], body["country"]) == ("en_gb", "gb")
    aggregations = [{"field": "country", "value": {"IRL": 3, "Netherlands": 40}}]
    if not body["selected_fields"]:
        return httpx.Response(200, json={"refineSearch": {"totalHits": 43, "data": {"jobs": [], "aggregations": aggregations}}})
    assert body["selected_fields"] == {"country": ["IRL"]}
    jobs = [
        {"jobId": str(n), "title": f"Site Engineer {n}", "location": "Dublin, IRL",
         "multi_location": ["Dublin, IRL", "Kill, Kildare, IRL"], "postedDate": "2026-09-01T00:00:00.000+0000",
         "category": "Engineering", "descriptionTeaser": "Teaser"}
        for n in range(3)
    ][body["from"]:body["from"] + body["size"]]
    return httpx.Response(200, json={"refineSearch": {"totalHits": 3, "data": {"jobs": jobs, "aggregations": aggregations}}})


@respx.mock
def test_phenom_filters_by_the_sites_own_irish_facet_value(monkeypatch):
    """BAM's facet names Ireland "IRL"; the value is read from the facets, not assumed."""
    from jobfinder.sources.base import BaseAdapter
    from jobfinder.sources.phenom import PhenomAdapter

    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))
    respx.get("https://careers.acme.com/").mock(return_value=httpx.Response(200, text=PHENOM_HOME))
    respx.post("https://careers.acme.com/widgets").mock(side_effect=_phenom_search)

    result = PhenomAdapter().fetch("careers.acme.com")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 3
    job = result.jobs[0]
    assert job.url == "https://careers.acme.com/gb/en/job/0"
    assert (job.location_raw, job.extra_locations) == ("Dublin, IRL", ["Kill, Kildare, IRL"])
    assert job.description == "<p>Full advert 0</p>"


@respx.mock
def test_phenom_response_without_facets_fails_rather_than_emptying_the_board():
    from jobfinder.sources.phenom import PhenomAdapter

    respx.get("https://careers.acme.com/").mock(return_value=httpx.Response(200, text=PHENOM_HOME))
    respx.post("https://careers.acme.com/widgets").mock(
        return_value=httpx.Response(200, json={"refineSearch": {"status": 500, "data": {}}})
    )

    assert PhenomAdapter().fetch("careers.acme.com").status is CrawlStatus.FAILED


# ---------------------------------------------------------------------------
# CoreHR
# ---------------------------------------------------------------------------


def _corehr_page(refs: list[str], total: int, next_start: int | None) -> str:
    rows = "".join(
        f'<tr><td class="erq_searchv4_result_row"><table><tr><td class="erq_searchv4_heading4">'
        f'<a class="erq_searchv4_big_anchor" href="javascript:viewTheJobSpec(\'{ref}\')">Lecturer {ref} (Ref: X/1)</a></td>'
        f'<td><a href="javascript:applyForJob(\'{ref}\', \'N\')">Apply</a></td></tr>'
        f'<tr><td>Job Ref :</td><td>{ref}</td><td>Close Date :</td><td>30-Sep-2026</td></tr>'
        f'<tr><td>Salary :</td><td>&euro;52,519 - &euro;67,129</td><td>Dept :</td><td>School of Law</td></tr>'
        f'<script>var links = 1;</script></table></td></tr>'
        for ref in refs
    )
    forward = (
        '<form name="searchv4navigateresultsforward" action="x" method="post">'
        f'<input name="p_start_from" type="hidden" value="{next_start}"></form>'
        if next_start is not None else ""
    )
    return f"<td>Your search returned {total} results</td><table>{rows}</table>{forward}"


@respx.mock
def test_corehr_follows_the_tenants_own_page_size():
    """Maynooth shows eight a page; assuming ten skipped two roles on every page."""
    from urllib.parse import parse_qs

    from jobfinder.sources.corehr import CoreHRAdapter

    def serve(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        assert form["p_competition_type"] == "ALLOPTIONS" and "p_keywords" in form
        start = form.get("p_start_from")
        if start is None:
            return httpx.Response(200, text=_corehr_page(["001", "002"], total=3, next_start=2))
        assert start == "2"
        return httpx.Response(200, text=_corehr_page(["003"], total=3, next_start=None))

    respx.post("https://my.corehr.com/pls/acmerecruit/erq_search_version_4.start_search_with_params").mock(
        side_effect=serve
    )

    result = CoreHRAdapter().fetch("acmerecruit|1|Dublin, Ireland")

    assert result.status is CrawlStatus.OK
    assert [j.source_job_id for j in result.jobs] == ["001", "002", "003"]
    job = result.jobs[0]
    assert job.title == "Lecturer 001 (Ref: X/1)"
    assert job.location_raw == "Dublin, Ireland"
    assert job.department == "School of Law"
    assert "Salary: €52,519 - €67,129" in job.description
    assert "var links" not in job.description
    assert job.url.startswith("https://my.corehr.com/pls/acmerecruit/erq_search_package.search_form")


@respx.mock
def test_corehr_tenant_without_a_results_page_fails():
    from jobfinder.sources.corehr import CoreHRAdapter

    respx.post(url__startswith="https://my.corehr.com/pls/gone/").mock(
        return_value=httpx.Response(200, text="<html>CoreError Page</html>")
    )
    assert CoreHRAdapter().fetch("gone|1|Dublin").status is CrawlStatus.FAILED


# ---------------------------------------------------------------------------
# Cornerstone
# ---------------------------------------------------------------------------

CSOD_HOME = (
    '<script>csod.context={"corp":"acme","cultureID":2,"cultureName":"en-GB",'
    '"endpoints":{"cloud":"https://uk.api.csod.com/","api":"/"},"token":"tok123"};\n</script>'
)


@respx.mock
def test_cornerstone_reads_the_regional_api_named_by_the_page():
    import json as _json

    from jobfinder.sources.cornerstone import CornerstoneAdapter

    respx.get(url__startswith="https://acme.csod.com/ux/ats/careersite/5/home").mock(
        return_value=httpx.Response(200, text=CSOD_HOME)
    )

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok123"
        body = _json.loads(request.content)
        ids = list(range(30))[(body["pageNumber"] - 1) * 25: body["pageNumber"] * 25]
        return httpx.Response(200, json={"data": {"totalCount": 30, "requisitions": [
            {"requisitionId": i, "displayJobTitle": f"Store Manager {i}", "postingEffectiveDate": "03/09/2026",
             "externalDescription": "Lead the store", "locations": [{"city": "Mary St", "state": "Dublin City", "country": "IE"}]}
            for i in ids
        ]}})

    respx.post("https://uk.api.csod.com/rec-job-search/external/jobs").mock(side_effect=serve)

    result = CornerstoneAdapter().fetch("acme|5")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 30
    job = result.jobs[0]
    assert job.url == "https://acme.csod.com/ux/ats/careersite/5/home/requisition/0?c=acme"
    assert job.location_raw == "Mary St, Dublin City, IE"
    assert (job.posted_at.day, job.posted_at.month) == (3, 9)  # en-GB dates are day first


def _icims_classic_page(ids: list[int], last: int, location: str | None = "IE-Dublin-Dublin") -> str:
    rows = "".join(
        '<li class="iCIMS_JobCardItem"><div class="row">'
        + (f'<div class="col-xs-6 header left"><span class="sr-only field-label">Location</span><span > {location}</span></div>' if location else "")
        + f'<div class="col-xs-12 title"><a href="https://careers-acme.icims.com/jobs/{i}/site-engineer/job?in_iframe=1" class="iCIMS_Anchor" title="{i} - Site Engineer">'
        f'<span class="sr-only field-label">Title</span><h3 > Site Engineer {i}</h3></a></div>'
        f'<div class="col-xs-12 description"> Build &amp; things</div></div></li>'
        for i in ids
    )
    pager = "".join(f'<a href="https://careers-acme.icims.com/jobs/search?pr={n}&amp;in_iframe=1">{n + 1}</a>' for n in range(last + 1))
    return f"<ul>{rows}</ul><div class='iCIMS_Paging'>{pager}</div>"


@respx.mock
def test_icims_classic_portal_is_read_page_by_page():
    """Sisk's portal lists its own roles rather than bouncing to a branded site."""
    from jobfinder.sources.icims import ICIMSAdapter

    pages = {"0": _icims_classic_page([1, 2], last=1), "1": _icims_classic_page([3], last=1)}
    respx.get("https://careers-acme.icims.com/jobs/search").mock(
        side_effect=lambda r: httpx.Response(200, text=pages[r.url.params["pr"]])
    )

    result = ICIMSAdapter().fetch("classic:careers-acme")

    assert result.status is CrawlStatus.OK
    assert [j.source_job_id for j in result.jobs] == ["1", "2", "3"]
    job = result.jobs[0]
    assert job.location_raw == "Dublin-Dublin, IE"
    assert job.url == "https://careers-acme.icims.com/jobs/1/site-engineer/job"
    assert job.description == "Build & things"


@respx.mock
def test_icims_classic_portal_reads_a_missing_location_from_the_role():
    from jobfinder.sources.icims import ICIMSAdapter

    respx.get("https://careers-acme.icims.com/jobs/search").mock(
        return_value=httpx.Response(200, text=_icims_classic_page([7], last=0, location=None))
    )
    respx.get(url__startswith="https://careers-acme.icims.com/jobs/7/").mock(
        return_value=httpx.Response(200, text="<dt>Job Locations</dt><dd><span>IE-Limerick</span></dd>")
    )

    result = ICIMSAdapter().fetch("classic:careers-acme")

    assert result.jobs[0].location_raw == "Limerick, IE"


# ---------------------------------------------------------------------------
# Rezoomo
# ---------------------------------------------------------------------------


@respx.mock
def test_rezoomo_reads_the_company_pages_job_list():
    from jobfinder.sources.rezoomo import API_URL, RezoomoAdapter

    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"success": True, "data": {
        "company": {"name": "Acme Stores"},
        "companyJobs": [
            {"id": 104521, "name": "Supervisor", "location": "Lusk, County Dublin, Ireland",
             "postDate": "September, 25 2026 15:51:49", "description": "<p>Lead the shift</p>",
             "salary": "€14.50 per hour", "isPublished": True, "scope": ["public"]},
            {"id": 104522, "name": "Internal Role", "location": "Cork", "isPublished": True, "scope": ["internal"]},
        ],
    }}))

    result = RezoomoAdapter().fetch("acme-stores")

    assert result.status is CrawlStatus.OK
    [job] = result.jobs
    assert job.url == "https://www.rezoomo.com/job/104521/"
    assert job.location_raw == "Lusk, County Dublin, Ireland"
    assert job.description.startswith("<p>Salary: €14.50 per hour</p>")
    assert (job.posted_at.year, job.posted_at.month, job.posted_at.day) == (2026, 9, 25)


@respx.mock
def test_rezoomo_unknown_company_fails_rather_than_emptying_the_board():
    from jobfinder.sources.rezoomo import API_URL, RezoomoAdapter

    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"success": False, "data": {}}))
    assert RezoomoAdapter().fetch("nobody").status is CrawlStatus.FAILED
