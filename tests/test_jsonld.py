"""Generic `schema.org/JobPosting` extraction.

Unlike the ATS adapters, this one parses markup written by thousands of different
publishers, so the tests are mostly about surviving the shapes real sites emit rather
than one vendor's documented schema.
"""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.jsonld import (
    JsonLdAdapter,
    format_address,
    iter_ld_objects,
    job_locations,
    parse_job_posting,
    title_from_url,
)

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"


def _page(*postings: str) -> str:
    blocks = "".join(
        f'<script type="application/ld+json">{p}</script>' for p in postings
    )
    return f"<html><head>{blocks}</head><body></body></html>"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_numeric_identifier_is_read_rather_than_falling_through_to_the_company():
    """Regression: every posting on a board collapsing to one job.

    Publishers emit `identifier` as a PropertyValue whose `value` is an integer job id
    and whose `name` is the *company*. Ignoring the integer sends the parse down its
    fallback path, every posting on the site gets the company name as its source id,
    and the crawl stores exactly one job for the whole board.
    """
    first = parse_job_posting(
        {
            "@type": "JobPosting",
            "title": "Product Designer",
            "identifier": {"@type": "PropertyValue", "name": "Nmbrs BV", "value": 2727516},
        },
        "https://example.com/o/product-designer",
    )
    second = parse_job_posting(
        {
            "@type": "JobPosting",
            "title": "Finance Administrator",
            "identifier": {"@type": "PropertyValue", "name": "Nmbrs BV", "value": 2699686},
        },
        "https://example.com/o/finance-administrator",
    )

    assert first is not None and second is not None
    assert first.source_job_id == "2727516"
    assert second.source_job_id == "2699686"
    assert first.source_job_id != second.source_job_id


def test_identifier_falls_back_to_the_url_not_the_company_name():
    job = parse_job_posting(
        {
            "@type": "JobPosting",
            "title": "Engineer",
            "identifier": {"@type": "PropertyValue", "name": "Acme Ltd"},
        },
        "https://example.com/o/engineer",
    )
    assert job is not None
    assert job.source_job_id == "https://example.com/o/engineer"


def test_empty_title_is_recovered_from_the_url_slug():
    """Real sites ship `"title": ""`. Dropping those loses genuine openings."""
    job = parse_job_posting(
        {"@type": "JobPosting", "title": "", "identifier": 42},
        "https://example.com/o/customer-care-specialist-3",
    )
    assert job is not None
    assert job.title == "Customer Care Specialist"


def test_a_posting_with_no_recoverable_title_is_skipped():
    assert parse_job_posting({"@type": "JobPosting"}, "https://example.com/") is None


def test_title_from_url_strips_numeric_suffixes_and_ignores_bare_ids():
    assert title_from_url("https://x.com/o/product-designer-3") == "Product Designer"
    assert title_from_url("https://x.com/jobs/12345") is None


def test_graph_wrapped_documents_are_flattened():
    """Many CMS plugins nest everything under @graph rather than emitting a list."""
    html = _page(
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"Organization","name":"Acme"},'
        '{"@type":"JobPosting","title":"Backend Engineer","identifier":7}]}'
    )
    types = [obj.get("@type") for obj in iter_ld_objects(html)]
    assert "JobPosting" in types
    assert "Organization" in types


def test_malformed_ld_block_does_not_kill_the_page():
    html = _page("{not json at all", '{"@type":"JobPosting","title":"Analyst"}')
    titles = [obj.get("title") for obj in iter_ld_objects(html) if obj.get("title")]
    assert titles == ["Analyst"]


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def test_nested_postal_address_is_flattened():
    place = {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Dublin",
            "addressRegion": "Leinster",
            "addressCountry": "IE",
        },
    }
    assert format_address(place) == "Dublin, Leinster, IE"


def test_country_given_as_an_object_is_read():
    place = {
        "address": {
            "addressLocality": "Dublin",
            "addressCountry": {"@type": "Country", "name": "Ireland"},
        }
    }
    assert format_address(place) == "Dublin, Ireland"


def test_multiple_job_locations_are_per_posting_and_expanded():
    """A list here is genuinely per-posting, unlike Greenhouse's company-wide offices."""
    primary, extras = job_locations(
        {
            "jobLocation": [
                {"address": {"addressLocality": "London", "addressCountry": "GB"}},
                {"address": {"addressLocality": "Dublin", "addressCountry": "IE"}},
            ]
        }
    )
    assert primary == "London, GB"
    assert extras == ["Dublin, IE"]


def test_remote_posting_with_no_place_is_marked_remote():
    primary, _ = job_locations({"jobLocationType": "TELECOMMUTE"})
    assert primary == "Remote"


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------


@respx.mock
def test_discovers_jobs_via_sitemap_and_extracts_them():
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text=ROBOTS_ALLOW)
    )
    respx.get("https://acme.ie/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<urlset>"
                "<url><loc>https://acme.ie/about</loc></url>"
                "<url><loc>https://acme.ie/jobs/backend-engineer</loc></url>"
                "<url><loc>https://acme.ie/jobs/data-analyst</loc></url>"
                "</urlset>"
            ),
        )
    )
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/jobs/backend-engineer").mock(
        return_value=httpx.Response(
            200,
            text=_page(
                '{"@type":"JobPosting","title":"Backend Engineer","identifier":1,'
                '"jobLocation":{"address":{"addressLocality":"Dublin",'
                '"addressCountry":"IE"}},"datePosted":"2026-03-01"}'
            ),
        )
    )
    respx.get("https://acme.ie/jobs/data-analyst").mock(
        return_value=httpx.Response(
            200,
            text=_page('{"@type":"JobPosting","title":"Data Analyst","identifier":2}'),
        )
    )

    result = JsonLdAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.OK
    titles = sorted(job.title for job in result.jobs)
    assert titles == ["Backend Engineer", "Data Analyst"]
    assert "about" not in " ".join(job.url for job in result.jobs)


@respx.mock
def test_robots_disallow_stops_the_crawl():
    """This adapter visits sites that never invited it, so robots.txt is binding."""
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    result = JsonLdAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.FAILED
    assert "robots" in (result.error or "").lower()


@respx.mock
def test_unreachable_robots_is_treated_as_refusal_not_permission():
    """Otherwise the crawler is least restrained against the sites least able to serve."""
    respx.get("https://acme.ie/robots.txt").mock(
        side_effect=httpx.ConnectError("unreachable")
    )
    assert JsonLdAdapter().fetch("https://acme.ie/careers").status is CrawlStatus.FAILED


@respx.mock
def test_missing_robots_file_means_no_restrictions():
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(
            200, text=_page('{"@type":"JobPosting","title":"Engineer","identifier":9}')
        )
    )

    result = JsonLdAdapter().fetch("https://acme.ie/careers")
    assert result.status is CrawlStatus.OK
    assert result.jobs[0].title == "Engineer"


@respx.mock
def test_offsite_links_are_not_followed():
    """A "powered by" footer must not turn into a crawl of the vendor's own site."""
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="https://vendor.example/jobs/theirs">Powered by</a>'
                '<a href="/jobs/ours">Ours</a>'
                + _page('{"@type":"JobPosting","title":"Listing","identifier":1}')
            ),
        )
    )
    offsite = respx.get("https://vendor.example/jobs/theirs").mock(
        return_value=httpx.Response(200, text=_page('{"@type":"JobPosting","title":"X"}'))
    )
    respx.get("https://acme.ie/jobs/ours").mock(
        return_value=httpx.Response(
            200, text=_page('{"@type":"JobPosting","title":"Ours","identifier":2}')
        )
    )

    result = JsonLdAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.OK
    assert not offsite.called
    assert "Ours" in [job.title for job in result.jobs]


@respx.mock
def test_a_site_with_no_job_markup_fails_rather_than_reporting_zero_jobs():
    """FAILED leaves existing jobs alone; a bare empty list would close them all."""
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(200, text="<html><body>no structured data</body></html>")
    )

    assert JsonLdAdapter().fetch("https://acme.ie/careers").status is CrawlStatus.FAILED


@respx.mock
def test_a_capped_crawl_is_partial_so_jobs_outside_the_sample_are_not_closed(monkeypatch):
    """Regression: a 60-page sample was reported as the whole board.

    The reconciler closes a job only on an OK fetch, reading absence as removal. On a large
    site the extractor reads a *sample* of job pages, and every crawl that sampled
    differently closed the roles it happened to skip - Cisco lost 48 real postings.
    """
    from jobfinder.sources import jsonld

    monkeypatch.setattr(JsonLdAdapter, "polite_pause", staticmethod(lambda: None))
    total = jsonld.MAX_JOB_PAGES + 5

    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text="<urlset>"
            + "".join(f"<url><loc>https://acme.ie/jobs/role-{i}</loc></url>" for i in range(total))
            + "</urlset>",
        )
    )
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://acme\.ie/jobs/role-\d+").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=_page(
                '{"@type":"JobPosting","title":"Role","identifier":%s}'
                % request.url.path.rsplit("-", 1)[-1]
            ),
        )
    )

    result = JsonLdAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == jsonld.MAX_JOB_PAGES


@respx.mock
def test_unread_nested_sitemaps_make_a_small_sample_partial_too(monkeypatch):
    """Fewer than the page ceiling is still a sample when job sitemaps went unfetched."""
    from jobfinder.sources import jsonld

    monkeypatch.setattr(JsonLdAdapter, "polite_pause", staticmethod(lambda: None))
    nested = jsonld.MAX_SITEMAP_FETCHES + 2

    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text="<sitemapindex>"
            + "".join(
                f"<sitemap><loc>https://acme.ie/jobs/sitemap-{i}.xml</loc></sitemap>"
                for i in range(nested)
            )
            + "</sitemapindex>",
        )
    )
    respx.get("https://acme.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://acme\.ie/jobs/sitemap-\d+\.xml").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text="<urlset><url><loc>https://acme.ie/jobs/role-%s</loc></url></urlset>"
            % request.url.path.rsplit("-", 1)[-1].removesuffix(".xml"),
        )
    )
    respx.get(url__regex=r"https://acme\.ie/jobs/role-\d+").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=_page(
                '{"@type":"JobPosting","title":"Role","identifier":%s}'
                % request.url.path.rsplit("-", 1)[-1]
            ),
        )
    )

    result = JsonLdAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.PARTIAL
    assert 0 < len(result.jobs) < jsonld.MAX_JOB_PAGES
