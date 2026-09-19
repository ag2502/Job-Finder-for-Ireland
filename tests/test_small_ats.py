"""Adapters for the smaller boards found behind blocked careers pages.

Payload shapes are trimmed from the live APIs (HireHive's own board, Breezy's demo board,
CyberSentriq's Occupop page and Pinpoint's own board, September 2026).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from jobfinder.core.models import Company, CoverageState, CrawlStatus
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.breezy import BreezyAdapter
from jobfinder.sources.hirehive import HireHiveAdapter
from jobfinder.sources.occupop import GATEWAY, OccupopAdapter
from jobfinder.sources.pinpoint import PinpointAdapter


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def _hirehive_page(items, has_next):
    return {"meta": {"has_next_page": has_next}, "items": items}


def _hirehive_item(job_id, title, city="Dublin", country="Ireland"):
    return {
        "id": job_id,
        "title": title,
        "location": city,
        "country": {"name": country, "code": "IE"},
        "description": {"html": "<p>Do things</p>", "text": "Do things"},
        "published_date": "2026-09-01T09:00:00Z",
        "hosted_url": f"https://acme.hirehive.com/{job_id}",
        "category": None,
    }


@respx.mock
def test_hirehive_reads_every_page():
    route = respx.get("https://acme.hirehive.com/api/v2/jobs")
    route.side_effect = [
        httpx.Response(200, json=_hirehive_page([_hirehive_item("a", "Engineer")], True)),
        httpx.Response(200, json=_hirehive_page([_hirehive_item("b", "Analyst", "Cork")], False)),
    ]

    result = HireHiveAdapter().fetch("acme")

    assert result.status is CrawlStatus.OK
    assert [(j.title, j.location_raw) for j in result.jobs] == [
        ("Engineer", "Dublin, Ireland"),
        ("Analyst", "Cork, Ireland"),
    ]


@respx.mock
def test_breezy_takes_the_description_from_the_position_page():
    respx.get("https://acme.breezy.hr/json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "p1",
                    "name": "Support Engineer",
                    "url": "https://acme.breezy.hr/p/p1-support-engineer",
                    "published_date": "2026-09-01T09:00:00Z",
                    "location": {"city": "Dublin", "country": {"name": "Ireland"}},
                    "locations": [
                        {"city": "Dublin", "country": {"name": "Ireland"}},
                        {"city": "London", "country": {"name": "United Kingdom"}},
                    ],
                    "department": "Support",
                    "company": {"name": "Acme"},
                }
            ],
        )
    )
    respx.get("https://acme.breezy.hr/p/p1-support-engineer").mock(
        return_value=httpx.Response(200, html='<div class="description"><p>Help people</p></div>')
    )

    job = BreezyAdapter().fetch("acme").jobs[0]

    assert job.location_raw == "Dublin, Ireland"
    assert job.extra_locations == ["London, United Kingdom"]
    assert "Help people" in (job.description or "")


@respx.mock
def test_occupop_reads_the_gateway_and_builds_the_apply_link():
    respx.post(GATEWAY).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "careersPage": {
                        "liveJobs": [
                            {
                                "uuid": "u-1",
                                "title": "Customer Success Manager",
                                "description": "<p>Own relationships</p>",
                                "publishedAt": "2026-09-17 09:17:10",
                                "companyName": "CyberSentriq",
                                "location": {"city": "Dublin", "country": "Ireland"},
                                "subsectors": [{"name": "Customer Success"}],
                            }
                        ]
                    }
                }
            },
        )
    )

    job = OccupopAdapter().fetch("cybersentriq").jobs[0]

    assert job.url == "https://cybersentriq.occupop-careers.com/jobs/u-1/apply"
    assert job.location_raw == "Dublin, Ireland"
    assert job.department == "Customer Success"


@respx.mock
def test_occupop_unknown_company_key_is_a_failure_not_an_empty_board():
    """An empty result would close every job the board ever had."""
    respx.post(GATEWAY).mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "Invalid company key!"}], "data": None}
        )
    )
    assert OccupopAdapter().fetch("nobody").status is CrawlStatus.FAILED


@respx.mock
def test_pinpoint_joins_the_advert_sections_in_order():
    respx.get("https://acme.pinpointhq.com/postings.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "title": "Legal Counsel",
                        "url": "https://acme.pinpointhq.com/en/postings/1",
                        "description": "<p>Intro</p>",
                        "key_responsibilities": "<ul><li>Contracts</li></ul>",
                        "key_responsibilities_header": "What you'll do",
                        "workplace_type": "remote",
                        "location": {"city": "Dublin", "province": "Dublin"},
                        "job": {"department": {"name": "Legal"}},
                    }
                ]
            },
        )
    )

    job = PinpointAdapter().fetch("acme").jobs[0]

    assert job.location_raw == "Dublin, Remote"
    assert job.department == "Legal"
    assert job.description.index("Intro") < job.description.index("What you'll do")


# ---------------------------------------------------------------------------
# Whose board is it?
# ---------------------------------------------------------------------------


def _company(name: str, website: str) -> Company:
    return Company(name=name, normalized_name=name.lower(), website=website)


@respx.mock
def test_a_sister_brands_board_is_not_filed_under_the_company():
    from jobfinder.registry.bulk_detect import board_belongs_to

    respx.post(GATEWAY).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"careersPage": {"liveJobs": [{"uuid": "u", "companyName": "CyberSentriq"}]}}},
        )
    )
    assert not board_belongs_to("occupop", "cybersentriq", _company("TitanHQ", "titanhq.com"), None)


@respx.mock
def test_a_board_that_names_the_company_is_accepted():
    from jobfinder.registry.bulk_detect import board_belongs_to

    respx.get("https://acme.breezy.hr/json").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "company": {"name": "Acme Ltd"}}])
    )
    assert board_belongs_to("breezy", "acme", _company("Acme", "acme.ie"), None)


def test_an_unnamed_board_must_resemble_the_company():
    from jobfinder.registry.bulk_detect import board_belongs_to

    nuritas = _company("Nuritas", "https://nuritas.com")
    assert board_belongs_to("hirehive", "nuritas", nuritas, None)
    assert not board_belongs_to("hirehive", "hellozai", _company("CurrencyFair", "https://currencyfair.com"), None)


def test_platforms_outside_the_check_are_unaffected(session: Session):
    from jobfinder.registry.bulk_detect import board_belongs_to

    assert board_belongs_to("greenhouse", "anything", _company("Acme", "acme.ie"), None)


def test_a_slug_that_spells_out_the_name_is_accepted():
    from jobfinder.registry.bulk_detect import board_belongs_to

    firm = _company("Mason Hayes & Curran", "https://mhc.ie")
    assert board_belongs_to("hirehive", "mason-hayes-and-curran", firm, None)
    assert not board_belongs_to("hirehive", "mason-recruitment", firm, None)


def test_blocked_companies_are_rechecked_weekly_not_monthly(session: Session):
    """New fingerprints and adapters only help the blocked queue if it is looked at again."""
    from datetime import timedelta

    from jobfinder.core.models import utcnow
    from jobfinder.registry.bulk_detect import pending_companies

    ten_days_ago = utcnow() - timedelta(days=10)
    for name, state in (("Blocked Co", CoverageState.BLOCKED), ("Plain Co", CoverageState.NO_CAREERS_PAGE)):
        session.add(
            Company(
                name=name,
                normalized_name=name.lower(),
                website=f"https://{name.split()[0].lower()}.ie",
                coverage_state=state,
                detection_checked_at=ten_days_ago,
            )
        )
    session.flush()

    assert [c.name for c in pending_companies(session)] == ["Blocked Co"]


# ---------------------------------------------------------------------------
# Eightfold
# ---------------------------------------------------------------------------


@respx.mock
def test_eightfold_reads_irish_roles_with_the_domain_from_the_careers_page():
    from jobfinder.sources.eightfold import EightfoldAdapter

    respx.get("https://acme.eightfold.ai/careers").mock(
        return_value=httpx.Response(200, html='<a href="/careers?domain=acme.com">x</a>' * 3)
    )
    search = respx.get("https://acme.eightfold.ai/api/pcsx/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "count": 1,
                    "positions": [
                        {
                            "id": 7,
                            "name": "Quality Engineer",
                            "locations": ["Galway, Ireland", "Dublin, Ireland"],
                            "postedTs": 1788393600,
                            "department": "Quality",
                            "positionUrl": "/careers/job/7",
                        }
                    ],
                }
            },
        )
    )
    respx.get("https://acme.eightfold.ai/api/pcsx/position_details").mock(
        return_value=httpx.Response(200, json={"data": {"jobDescription": "<p>Test things</p>"}})
    )

    job = EightfoldAdapter().fetch("acme").jobs[0]

    params = search.calls[0].request.url.params
    assert (params["domain"], params["location"]) == ("acme.com", "Ireland")
    assert job.url == "https://acme.eightfold.ai/careers/job/7"
    assert job.extra_locations == ["Dublin, Ireland"]
    assert job.description == "<p>Test things</p>"


def test_eightfold_sandbox_link_resolves_to_the_live_tenant():
    from jobfinder.registry.detect import detect_in_text

    assert detect_in_text("https://hp-sandbox.eightfold.ai/careers") == ("eightfold", "hp")


def test_an_eightfold_test_tenant_is_never_crawled():
    from jobfinder.sources.eightfold import EightfoldAdapter

    assert EightfoldAdapter().fetch("hp-sandbox").status is CrawlStatus.FAILED


def test_a_two_letter_tenant_that_is_the_companys_domain_is_theirs():
    from jobfinder.registry.bulk_detect import board_belongs_to

    assert board_belongs_to("eightfold", "hp", _company("HP", "https://hp.com"), None)


def test_a_vendor_cdn_host_is_never_a_slug():
    from jobfinder.registry.detect import detect_in_text

    assert detect_in_text('<script src="https://cdn13.icims.com/x.js"></script>') is None
