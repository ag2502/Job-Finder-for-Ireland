"""Reading vacancies straight from a careers page's HTML.

There is no markup to lean on here, so most of these tests are about what must *not* be
taken for a job: menus, advice pages, category links and news items all come in tidy
groups of sibling links, and every one of them was mistaken for a vacancy list by an
earlier version of the heuristics when run against the real blocked queue.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.careers_html import (
    CareersHtmlAdapter,
    Link,
    job_candidates,
    page_links,
    parse_job_page,
)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def _listing(*items: tuple[str, str]) -> str:
    rows = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in items)
    return f"<html><body><nav><a href='/about'>About</a></nav><ul>{rows}</ul></body></html>"


def _advert(title: str, body: str = "Location: Dublin 2\nClosing date: 1 October") -> str:
    return (
        f"<html><body><header>Menu</header><main><h1>{title}</h1>"
        f"<p>{body}</p><a href='/apply'>Apply now</a></main></body></html>"
    )


def _mock_site(listing_html: str, adverts: dict[str, str]) -> None:
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(200, html=listing_html)
    )
    for path, html in adverts.items():
        respx.get(f"https://acme.ie{path}").mock(return_value=httpx.Response(200, html=html))


# ---------------------------------------------------------------------------
# Recognising a vacancy list
# ---------------------------------------------------------------------------


def test_sibling_links_with_role_titles_are_a_vacancy_list():
    links = page_links(
        _listing(
            ("/vacancies/staff-nurse", "Staff Nurse"),
            ("/vacancies/senior-porter", "Senior Porter"),
            ("/vacancies/finance-officer", "Finance Officer"),
        ),
        "https://acme.ie/careers",
    )
    assert [link.text for link in job_candidates(links, "https://acme.ie/careers")] == [
        "Staff Nurse",
        "Senior Porter",
        "Finance Officer",
    ]


def test_a_careers_menu_is_not_a_vacancy_list():
    """Siblings under /careers/ with descriptive slugs - the shape of every brochure."""
    links = page_links(
        _listing(
            ("/careers/making-applications", "Making Applications"),
            ("/careers/mentoring-programme", "Mentoring Programme"),
            ("/careers/careers-information", "Careers Information & Resources"),
        ),
        "https://acme.ie/careers",
    )
    assert job_candidates(links, "https://acme.ie/careers") == []


def test_links_carrying_a_posting_id_count_even_with_generic_text():
    links = page_links(
        _listing(("/job?jobId=101", "Click here"), ("/job?jobId=102", "Click here")),
        "https://acme.ie/careers",
    )
    assert len(job_candidates(links, "https://acme.ie/careers")) == 2


def test_a_real_title_starting_with_a_navigation_word_is_kept():
    """`Graduate` is a menu item on its own but the start of many real roles."""
    links = page_links(
        _listing(
            ("/vacancies/1", "Graduate Engineer"),
            ("/vacancies/2", "Graduate Accountant"),
        ),
        "https://acme.ie/careers",
    )
    assert len(job_candidates(links, "https://acme.ie/careers")) == 2


# ---------------------------------------------------------------------------
# Confirming a page is a job
# ---------------------------------------------------------------------------


def test_an_advert_is_read_with_its_labelled_location():
    link = Link("https://acme.ie/vacancies/1", "Staff Nurse", "Staff Nurse")
    job = parse_job_page(_advert("Staff Nurse"), link.url, link)
    assert job is not None
    assert job.title == "Staff Nurse"
    assert job.location_raw == "Dublin 2"


def test_a_page_with_nothing_to_apply_for_is_not_a_job():
    """The Irish Times' "Jobs in Co. Antrim" pages are tidy siblings with no advert."""
    link = Link("https://acme.ie/jobs/co-antrim", "Staff Officer", "")
    html = "<html><body><main><h1>Jobs in Co. Antrim</h1><p>12 results</p></main></body></html>"
    assert parse_job_page(html, link.url, link) is None


def test_a_news_story_that_mentions_applying_is_not_a_job():
    link = Link("https://acme.ie/news/1", "Read more", "")
    html = _advert("Sustainability fund launched", "Community groups can apply from May.")
    assert parse_job_page(html, link.url, link) is None


def test_a_layout_word_after_a_location_label_is_not_a_place():
    link = Link("https://acme.ie/vacancies/1", "Chef", "Chef | Location | Apply")
    html = _advert("Chef", "Location\nApply\nBased at our Dublin 8 kitchen.")
    job = parse_job_page(html, link.url, link)
    assert job is not None
    assert job.location_raw == "Dublin 8, Ireland"


# ---------------------------------------------------------------------------
# The adapter end to end
# ---------------------------------------------------------------------------


@respx.mock
def test_the_list_one_click_past_a_brochure_is_found():
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(
            200,
            html="<html><body><a href='/current-vacancies'>Current vacancies</a></body></html>",
        )
    )
    respx.get("https://acme.ie/current-vacancies").mock(
        return_value=httpx.Response(
            200,
            html=_listing(
                ("/vacancies/1", "Staff Nurse"), ("/vacancies/2", "Finance Officer")
            ),
        )
    )
    respx.get("https://acme.ie/vacancies/1").mock(
        return_value=httpx.Response(200, html=_advert("Staff Nurse"))
    )
    respx.get("https://acme.ie/vacancies/2").mock(
        return_value=httpx.Response(200, html=_advert("Finance Officer"))
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.OK
    assert sorted(job.title for job in result.jobs) == ["Finance Officer", "Staff Nurse"]


@respx.mock
def test_a_paginated_list_is_reported_as_partial():
    """Only the first page is read, and the roles on the rest must not be closed."""
    html = _listing(
        ("/vacancies/1", "Staff Nurse"),
        ("/vacancies/2", "Finance Officer"),
    ).replace("</ul>", "</ul><a href='/careers?page=2'>Next</a>")
    _mock_site(
        html,
        {"/vacancies/1": _advert("Staff Nurse"), "/vacancies/2": _advert("Finance Officer")},
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == 2


@respx.mock
def test_a_site_that_disallows_crawling_is_not_read():
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")
    assert result.status is CrawlStatus.FAILED
    assert "robots.txt" in (result.error or "")


def test_an_hr_page_about_staff_is_not_a_role():
    """UCC's "Staff Wellbeing" and "Staff Training" pages sat in its vacancy section."""
    link = Link("https://acme.ie/hr/wellbeing", "Staff Wellbeing", "")
    assert parse_job_page(_advert("Staff Wellbeing", "How to apply for support."), link.url, link) is None


def test_a_marked_up_advert_with_no_place_takes_dublin_from_its_title():
    link = Link("https://jobs.example.com/job/1", "", "")
    html = (
        '<script type="application/ld+json">{"@type":"JobPosting",'
        '"title":"Legal Advisor in Dublin","identifier":1}</script>'
    )
    job = parse_job_page(html, link.url, link)
    assert job is not None and job.location_raw == "Dublin, Ireland"
