"""Browser-assisted detection: what a rendered careers page is allowed to prove.

The browser itself is not exercised here; these tests cover the decision made from what
it recorded, which is where a wrong board could be registered.
"""

from __future__ import annotations

from jobfinder.registry.render_probe import Evidence, detection_from


def test_a_board_the_page_requests_is_detected():
    evidence = Evidence(
        requested=[
            "https://careers.adyen.com/app.js",
            "https://boards-api.greenhouse.io/v1/boards/adyen/offices",
        ],
        rendered="<div id=app></div>",
    )
    detection = detection_from(evidence, "https://careers.adyen.com")
    assert (detection.adapter, detection.slug) == ("greenhouse", "adyen")
    assert detection.note == "requested while rendering"


def test_a_requested_board_outranks_a_link_in_the_rendered_page():
    """Markup can link anywhere; the page's own requests are what it actually loads."""
    evidence = Evidence(
        requested=["https://mason-hayes-and-curran.hirehive.com/api/v1/jobs"],
        rendered='<a href="https://jobs.lever.co/someone-else">Partner</a>',
    )
    detection = detection_from(evidence, "https://www.mhc.ie/careers")
    assert (detection.adapter, detection.slug) == ("hirehive", "mason-hayes-and-curran")


def test_the_rendered_page_is_read_when_requests_name_nothing():
    evidence = Evidence(
        requested=["https://example.ie/main.js"],
        rendered='<a href="https://jobs.lever.co/acme">Open roles</a>',
    )
    detection = detection_from(evidence, "https://example.ie/careers")
    assert (detection.adapter, detection.note) == ("lever", "in rendered page")


def test_nothing_found_keeps_the_careers_page_as_blocked():
    detection = detection_from(Evidence(requested=["https://example.ie/a.js"]), "https://example.ie/careers")
    assert not detection.found
    assert detection.careers_url == "https://example.ie/careers"
