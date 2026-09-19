"""Sites excluded on principle stay excluded, wherever the URL comes from."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from jobfinder.core.models import Company, CoverageState, CrawlStatus
from jobfinder.sources.careers_html import CareersHtmlAdapter
from jobfinder.sources.jsonld import JsonLdAdapter
from jobfinder.sources.policy import is_excluded


@pytest.mark.parametrize(
    "url,excluded",
    [
        ("https://www.recruitireland.com/", True),
        ("https://www.linkedin.com/top-content/career/", True),
        ("https://ie.indeed.com/cmp/acme", True),
        ("irishjobs.ie/jobs", True),
        ("https://www.jobs.ie/", True),
        ("https://boards-api.greenhouse.io/v1/boards/linkedin", False),
        ("https://notjobs.ie/", False),
        ("https://www.irishtimes.com/", False),
    ],
)
def test_the_exclusion_list(url: str, excluded: bool):
    assert is_excluded(url) is excluded


@respx.mock
@pytest.mark.parametrize("adapter", [JsonLdAdapter(), CareersHtmlAdapter()])
def test_generic_readers_never_request_an_excluded_site(adapter):
    """The Irish Times' careers page is RecruitIreland, which it owns."""
    route = respx.get(url__regex=r".*recruitireland\.com.*").mock(
        return_value=httpx.Response(200, html="<a href='/jobs/1'>Engineer</a>")
    )
    result = adapter.fetch("https://www.recruitireland.com/")
    assert result.status is CrawlStatus.FAILED
    assert not route.called


def test_blocked_companies_on_excluded_sites_are_not_trial_extracted(session: Session):
    from jobfinder.registry.extraction import blocked_candidates

    for name, url in (("The Irish Times", "https://www.recruitireland.com/"), ("Acme", "https://acme.ie/careers")):
        session.add(
            Company(name=name, normalized_name=name.lower(), careers_url=url,
                    coverage_state=CoverageState.BLOCKED)
        )
    session.flush()
    assert [c.name for c in blocked_candidates(session)] == ["Acme"]


@respx.mock
def test_detection_never_visits_an_excluded_companys_own_site():
    from jobfinder.registry.detect import detect_for_website

    site = respx.get(url__regex=r".*linkedin\.com.*").mock(return_value=httpx.Response(200))
    respx.route(url__regex=r"^(?!.*linkedin\.com).*").mock(return_value=httpx.Response(404))

    detect_for_website("https://www.linkedin.com", name="LinkedIn")

    assert not site.called
