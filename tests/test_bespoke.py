"""Bespoke employer adapters, against response shapes recorded from the live sites."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.pipeline.state import resolve_location
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.bespoke import apple, ibm, tiktok


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))
    monkeypatch.setattr(apple.time, "sleep", lambda _: None)


def apple_page(total: int, ids: list[int]) -> str:
    search = {
        "totalRecords": total,
        "searchResults": [
            {
                "id": f"{n}-1418",
                "positionId": str(n),
                "postingTitle": f"Role {n}",
                "transformedPostingTitle": f"role-{n}",
                "postDateInGMT": "2026-09-25T09:21:20.720Z",
                "jobSummary": "Apple’s teams",
                "team": {"teamName": "Operations and Supply Chain"},
                "locations": [{"name": "Dublin", "countryName": "Ireland"}]
                + ([{"name": "Cork", "countryName": "Ireland"}] if n == 1 else []),
            }
            for n in ids
        ],
    }
    payload = json.dumps({"loaderData": {"search": search}})
    literal = json.dumps(payload)[1:-1]  # the page embeds it as a JS string literal
    return f'<script>window.__staticRouterHydrationData = JSON.parse("{literal}");</script>'


@respx.mock
def test_apple_reads_every_page_and_retries_a_transient_empty_one():
    responses = {
        "1": [apple_page(25, list(range(1, 21)))],
        # Page 2 first answers with the empty render seen on the live site.
        "2": [apple_page(0, []), apple_page(25, list(range(21, 26)))],
    }

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=responses[request.url.params["page"]].pop(0))

    respx.get(apple.SEARCH_URL).mock(side_effect=serve)

    result = apple.AppleAdapter().fetch("ireland-IRL")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 25
    first = next(j for j in result.jobs if j.source_job_id == "1-1418")
    assert first.url == "https://jobs.apple.com/en-ie/details/1-1418/role-1"
    assert (first.location_raw, first.extra_locations) == ("Dublin, Ireland", ["Cork, Ireland"])
    assert first.description == "Apple’s teams"
    assert first.posted_at.tzinfo is not None


@respx.mock
def test_apple_page_that_stays_empty_fails_rather_than_closing_roles():
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, text=apple_page(25, list(range(1, 21))))
        return httpx.Response(200, text=apple_page(0, []))

    respx.get(apple.SEARCH_URL).mock(side_effect=serve)

    assert apple.AppleAdapter().fetch("ireland-IRL").status is CrawlStatus.FAILED


def tiktok_post(n: int) -> dict:
    return {
        "id": str(7650000000000000000 + n),
        "title": f"Trust & Safety Specialist {n}",
        "description": "About the team",
        "requirement": "Minimum Qualifications",
        "job_category": {"en_name": "Operations"},
        "city_info": {
            "code": "CT_37", "en_name": "Dublin",
            "parent": {"code": "ST_22", "en_name": "Dublin",
                       "parent": {"code": "CN_9", "en_name": "Ireland", "parent": None}},
        },
    }


@respx.mock
def test_tiktok_pages_by_offset_and_joins_description_and_requirements():
    def serve(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["website-path"] == "tiktok"
        assert body["location_code_list"] == ["CT_37"]
        posts = [tiktok_post(n) for n in range(130)][body["offset"]:body["offset"] + body["limit"]]
        return httpx.Response(200, json={"code": 0, "data": {"job_post_list": posts, "count": 130}})

    respx.post(tiktok.SEARCH_URL).mock(side_effect=serve)

    result = tiktok.TikTokAdapter().fetch("CT_37")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 130
    job = result.jobs[0]
    assert job.location_raw == "Dublin, Ireland"
    assert resolve_location(job).is_dublin
    assert job.description == "About the team\n\nMinimum Qualifications"
    assert job.url == f"https://lifeattiktok.com/search/{job.source_job_id}"


@respx.mock
def test_tiktok_empty_answer_is_a_failure_not_an_empty_board():
    """Country and region codes, and unknown codes, all answer with no roles."""
    respx.post(tiktok.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"code": 0, "data": {"job_post_list": [], "count": 0}})
    )
    assert tiktok.TikTokAdapter().fetch("CN_9").status is CrawlStatus.FAILED


def ibm_hit(n: int, office: str = "Mulhuddart, IE", arrangement: str = "Hybrid") -> dict:
    return {"_source": {
        "url": f"https://careers.ibm.com/careers/JobDetail?jobId={n}",
        "title": f"Software Developer {n}",
        "description": "At IBM Software",
        "dcdate": "2026-09-14",
        "field_keyword_05": "Ireland",
        "field_keyword_08": "Software Engineering",
        "field_keyword_17": arrangement,
        "field_keyword_19": office,
    }}


@respx.mock
def test_ibm_pages_the_country_filter_and_reads_the_office():
    def serve(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["post_filter"] == {"term": {"field_keyword_05": "Ireland"}}
        hits = [ibm_hit(n) for n in range(150)] + [ibm_hit(999, "Dublin, IE", "Remote")]
        page = hits[body["from"]:body["from"] + body["size"]]
        return httpx.Response(200, json={"hits": {"total": {"value": len(hits)}, "hits": page}})

    respx.post(ibm.SEARCH_URL).mock(side_effect=serve)

    result = ibm.IBMAdapter().fetch("Ireland")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 151
    job = next(j for j in result.jobs if j.source_job_id == "0")
    # Mulhuddart is IBM's Dublin campus and names no city.
    assert resolve_location(job).is_dublin
    assert job.department == "Software Engineering"
    remote = next(j for j in result.jobs if j.source_job_id == "999")
    assert remote.location_raw == "Dublin, IE (Remote)"


def revolut_page(key: str, value) -> str:
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {key: value}}})
        + "</script>"
    )


@respx.mock
def test_revolut_keeps_irish_positions_and_leads_with_the_irish_office():
    from jobfinder.sources.bespoke import revolut

    positions = [
        {"id": "a1", "text": "Backend Engineer", "team": "Engineering", "locations": [
            {"name": "London", "type": "office", "country": "United Kingdom"},
            {"name": "Dublin", "type": "office", "country": "Ireland"},
            {"name": "Ireland - Remote", "type": "remote", "country": "Ireland"},
        ]},
        {"id": "b2", "text": "Analyst", "locations": [{"name": "Ireland - Remote", "type": "remote", "country": "Ireland"}]},
        {"id": "c3", "text": "Lawyer", "locations": [{"name": "Vilnius", "type": "office", "country": "Lithuania"}]},
    ]
    respx.get(revolut.CAREERS_URL).mock(return_value=httpx.Response(200, text=revolut_page("positions", positions)))
    respx.get(url__startswith="https://www.revolut.com/careers/position/").mock(
        return_value=httpx.Response(200, text=revolut_page("position", {"description": "<p>About Revolut</p>"}))
    )

    result = revolut.RevolutAdapter().fetch("Ireland")

    assert result.status is CrawlStatus.OK
    engineer, analyst = result.jobs
    assert engineer.location_raw == "Dublin, Ireland"
    assert engineer.extra_locations == ["Remote, Ireland", "London, United Kingdom"]
    assert resolve_location(engineer).is_dublin
    assert engineer.description == "<p>About Revolut</p>"
    assert resolve_location(analyst).is_remote


@respx.mock
def test_revolut_page_without_positions_fails():
    from jobfinder.sources.bespoke import revolut

    respx.get(revolut.CAREERS_URL).mock(return_value=httpx.Response(200, text=revolut_page("positions", [])))
    assert revolut.RevolutAdapter().fetch("Ireland").status is CrawlStatus.FAILED
