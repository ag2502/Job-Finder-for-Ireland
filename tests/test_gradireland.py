"""gradireland: the site's own search query, answered only with its front-end headers."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.boards import gradireland
from jobfinder.sources.boards.gradireland import SEARCH, GradIrelandAdapter, parse


def _doc(nid: str, **extra) -> dict:
    return {
        "nid": nid,
        "title": "Graduate Software Engineer",
        "path": f"/jobs/graduate-software-engineer-{nid}",
        "body": "<p>Build things</p>",
        "organisation": {"title": "Acme"},
        "opportunityType": ["Graduate scheme"],
        **extra,
    }


def test_a_listing_is_filed_under_its_employer_with_an_irish_location():
    job = parse(_doc("1", location="Tallaght", regions=["County Dublin", "Europe", "Ireland"]))
    assert job.company_name == "Acme"
    assert job.location_raw == "Tallaght, Ireland"
    assert job.url == "https://gradireland.com/jobs/graduate-software-engineer-1"
    assert job.department == "Graduate scheme"


def test_regions_stand_in_when_there_is_no_free_text_location():
    job = parse(_doc("2", regions=["County Dublin", "Europe", "Ireland"]))
    assert job.location_raw == "County Dublin, Ireland"


@respx.mock
def test_every_page_is_read_with_the_front_end_headers(monkeypatch):
    monkeypatch.setattr(gradireland, "PAGE_SIZE", 1)
    route = respx.post(SEARCH)
    route.side_effect = [
        httpx.Response(200, json={"search": {"result_count": 2, "documents": [_doc("1")]}}),
        httpx.Response(200, json={"search": {"result_count": 2, "documents": [_doc("2")]}}),
        httpx.Response(200, json={"search": {"result_count": 2, "documents": []}}),
    ]

    result = GradIrelandAdapter().fetch("all")

    assert result.status is CrawlStatus.OK
    assert sorted(j.source_job_id for j in result.jobs) == ["1", "2"]
    assert route.calls[0].request.headers["x-host"] == "users.gradireland.com"


@respx.mock
def test_an_empty_first_page_is_a_refusal_not_an_empty_board():
    """Without its headers the service answers 200 with nothing, which would close every job."""
    respx.post(SEARCH).mock(
        return_value=httpx.Response(200, json={"search": {"result_count": 0, "documents": []}})
    )
    assert GradIrelandAdapter().fetch("all").status is CrawlStatus.FAILED
