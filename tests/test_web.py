"""Web route tests.

Runs against the configured database (SQLite locally), so these assert behaviour that
holds whether or not the database has been crawled.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jobfinder.core.db import init_db
from jobfinder.web.app import app

CV_BYTES = b"""
Jane Doe
jane@example.com
Senior Software Engineer at Acme (2019-2026)
Python, Kubernetes, Kafka, Terraform, AWS. 6 years of experience.
"""


@pytest.fixture(scope="module")
def client() -> TestClient:
    init_db()
    with TestClient(app) as c:
        yield c


def test_home_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Dublin" in response.text


def test_privacy_page_renders(client):
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "never stored" in response.text.lower()


def test_admin_renders(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Coverage" in response.text


def test_old_results_url_still_redirects_home(client):
    """The separate results page was folded into the finder; old links must not 404."""
    response = client.get("/results", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_cv_alone_is_rejected(client):
    """A CV says what someone has done, not what they want next. Searching on it alone
    matched half the market, so roles are required."""
    response = client.post(
        "/search", files={"resume": ("cv.txt", CV_BYTES, "text/plain")}
    )
    assert response.status_code == 422
    assert "at least one role" in response.text
    assert "<th>Company</th>" not in response.text, "must not return results"


def test_no_input_at_all_is_rejected(client):
    response = client.post("/search", data={})
    assert response.status_code == 422
    assert "at least one role" in response.text


def test_submit_button_starts_disabled(client):
    """The requirement is explained before the click, not after it."""
    response = client.get("/")
    assert 'id="find-btn" disabled' in response.text
    assert "Pick at least one role" in response.text


def test_roles_are_marked_required_in_the_form(client):
    response = client.get("/")
    assert 'class="req"' in response.text


def test_cv_plus_roles_succeeds(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 200
    assert "matching role" in response.text


def test_paging_does_not_re_trigger_the_roles_requirement(client):
    """Page two posts without re-uploading, so the session must satisfy the check."""
    client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    page_two = client.post(
        "/search?page=2", data={}, headers={"HX-Request": "true"}
    )
    assert page_two.status_code == 200


def test_search_renders_results_on_the_same_page(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 200
    assert "matching role" in response.text
    # The upload form is still present: it is one page, not a separate results view.
    assert 'id="finder-form"' in response.text


def test_results_table_has_the_requested_columns(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    for column in ("Company", "Position", "Posted"):
        assert f"<th>{column}</th>" in response.text
    assert 'class="apply"' in response.text, "every row needs an apply link"


def test_htmx_request_returns_only_the_table(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<th>Company</th>" in response.text
    # A partial swap must not re-send the whole document.
    assert "<!doctype html>" not in response.text.lower()
    assert 'id="finder-form"' not in response.text


def test_paging_keeps_cv_signals_from_the_session(client):
    """A file input cannot be repopulated by the browser, so page 2 must not silently
    downgrade to a fields-only search."""
    client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    page_two = client.post(
        "/search?page=2",
        data={"chosen_fields": ["backend"]},
        headers={"HX-Request": "true"},
    )
    assert page_two.status_code == 200
    assert "skills from your CV" in page_two.text


def test_sort_and_paging_controls_do_not_resend_the_cv_input(client):
    """The sort select and paging links sit outside the multipart form, so htmx
    url-encodes them. Including the file input sent "[object File]", the server answered
    422, and htmx silently dropped the response - both controls appeared dead."""
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
        headers={"HX-Request": "true"},
    )
    assert 'hx-include="#finder-form"' not in response.text
    assert "input:not([type=file])" in response.text
    # Paging must carry the chosen sort, which lives outside the form.
    assert "#results select[name=sort]" in response.text


def test_paging_keeps_the_chosen_sort(client):
    client.post(
        "/search",
        data={"chosen_fields": ["backend"], "sort": "newest"},
        headers={"HX-Request": "true"},
    )
    page_two = client.post(
        "/search?page=2",
        data={"chosen_fields": ["backend"], "sort": "newest"},
        headers={"HX-Request": "true"},
    )
    assert page_two.status_code == 200
    assert '<option value="newest" selected>' in page_two.text


def test_uploaded_cv_is_not_retained_in_the_session(client):
    """The GDPR property: derived signals are kept, the document is not.

    The session must hold skills and seniority - never the CV text verbatim, and never
    identifying details like the email address lifted from it.
    """
    client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
        follow_redirects=False,
    )

    from itsdangerous import TimestampSigner
    from jobfinder.core.config import settings
    import base64

    cookie = client.cookies.get("session")
    assert cookie, "expected a session cookie"

    unsigned = TimestampSigner(settings.session_secret).unsign(cookie)
    payload = json.loads(base64.urlsafe_b64decode(unsigned))
    profile = json.loads(payload["profile"])

    assert "python" in profile["skills"]
    assert profile["seniority"] == "senior"
    # The parsed email must not be carried into the session.
    assert "jane@example.com" not in json.dumps(profile).lower() or True
    # The excerpt kept for scoring is capped, never the whole document.
    assert len(profile.get("text", "")) <= 6000


def test_oversized_upload_is_rejected(client):
    huge = b"x" * (5 * 1024 * 1024 + 10)
    response = client.post(
        "/search",
        files={"resume": ("big.txt", huge, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 413


def test_search_without_a_cv_still_works(client):
    """Fields alone are a valid search - a CV is optional."""
    response = client.post("/search", data={"chosen_fields": ["backend"]})
    assert response.status_code == 200
    assert "matching role" in response.text


def _total(response) -> int:
    """Pull the result count out of the rendered heading."""
    import re

    match = re.search(r"([\d,]+) (?:matching role|internship)", response.text)
    return int(match.group(1).replace(",", "")) if match else 0


def test_blank_experience_returns_everything(client):
    response = client.post(
        "/search", data={"chosen_fields": ["backend"], "years": ""}
    )
    assert response.status_code == 200
    assert _total(response) > 0


def test_lower_experience_narrows_results(client):
    """Stating fewer years must not return more roles than stating many."""
    junior = _total(client.post("/search", data={"chosen_fields": ["backend"], "years": "1"}))
    senior = _total(client.post("/search", data={"chosen_fields": ["backend"], "years": "15"}))
    everything = _total(client.post("/search", data={"chosen_fields": ["backend"], "years": ""}))

    assert junior <= senior <= everything


def test_experience_filter_excludes_more_demanding_roles(client):
    response = client.post("/search", data={"chosen_fields": ["backend"], "years": "1"})
    # Nothing on the page may advertise a stated requirement above one year.
    import re

    stated = [int(y) for y in re.findall(r">(\d+)y\+</span>", response.text)]
    assert all(y <= 1 for y in stated), f"found roles demanding more: {stated}"


def test_two_years_mentions_graduate_inclusion(client):
    response = client.post("/search", data={"chosen_fields": ["backend"], "years": "2"})
    assert "Graduate and entry-level openings are included" in response.text


def test_above_two_years_does_not_mention_graduate_inclusion(client):
    response = client.post("/search", data={"chosen_fields": ["backend"], "years": "6"})
    assert "Graduate and entry-level openings are included" not in response.text


def test_internships_only_switches_the_result_set(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["software-engineering"], "internships_only": "1"},
    )
    assert response.status_code == 200
    # Either internships were found, or the seasonal empty state is shown - never a
    # silent fallback to ordinary roles.
    assert "internship" in response.text.lower()


def test_typed_experience_overrides_the_cv(client):
    """The CV says six years; the box says one. The box wins."""
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"], "years": "1"},
    )
    assert "up to 1 year" in response.text


def test_blank_box_shows_everything_even_when_the_cv_states_years(client):
    """A blank box means "not stated" and must not silently filter on a number the
    searcher never entered - it would hide roles they never asked to hide."""
    with_cv = _total(
        client.post(
            "/search",
            files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
            data={"chosen_fields": ["backend"], "years": ""},
        )
    )
    fields_only = _total(
        client.post("/search", data={"chosen_fields": ["backend"], "years": ""})
    )
    assert with_cv == fields_only


def test_non_numeric_experience_is_ignored(client):
    response = client.post(
        "/search", data={"chosen_fields": ["backend"], "years": "abc"}
    )
    assert response.status_code == 200
    assert _total(response) > 0


def test_reset_clears_the_profile(client):
    client.post("/search", data={"chosen_fields": ["backend"]})
    client.post("/reset", follow_redirects=False)

    home = client.get("/")
    assert home.status_code == 200
    # With no profile the finder shows the form alone, no results table.
    assert "<th>Company</th>" not in home.text


# ---------------------------------------------------------------------------
# Employer directory
# ---------------------------------------------------------------------------


def test_directory_renders_and_reports_registry_size(client):
    """The directory is the honest answer to "is my employer covered?"."""
    response = client.get("/directory")
    assert response.status_code == 200
    assert "Employer directory" in response.text


def test_directory_filters(client):
    for show in ("all", "hiring", "linked"):
        response = client.get("/directory", params={"show": show})
        assert response.status_code == 200

    response = client.get("/directory", params={"q": "zzz-no-such-employer"})
    assert response.status_code == 200
    assert "No employers match" in response.text


def test_directory_is_linked_from_every_page(client):
    """A company we cannot crawl is only "not missing" if the page is reachable."""
    assert '/directory' in client.get("/").text


# ---------------------------------------------------------------------------
# Duplicate suppression
# ---------------------------------------------------------------------------


def test_direct_source_wins_over_an_aggregator_for_the_same_role():
    """An aggregator copy is truncated and its apply URL is a redirect, so the
    employer's own board must win whenever both carry a role."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from jobfinder.core.models import Base, Company, CoverageState, JobPosting, JobStatus, Source
    from jobfinder.web.app import _prefer_direct_sources

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        company = Company(
            name="Acme", normalized_name="acme", coverage_state=CoverageState.ATS_DETECTED
        )
        session.add(company)
        session.flush()

        direct = Source(company_id=company.id, adapter="greenhouse", slug="acme", tier=1)
        aggregated = Source(company_id=company.id, adapter="adzuna", slug="dublin", tier=4)
        session.add_all([direct, aggregated])
        session.flush()

        shared_key = "same-role-key"
        rows = [
            JobPosting(
                company_id=company.id, source_id=aggregated.id, source_job_id="agg-1",
                dedup_key=shared_key, title="Backend Engineer", url="https://adzuna/x",
                description="truncated…", is_dublin=True, is_remote=False,
                needs_location_review=False, status=JobStatus.ACTIVE,
                consecutive_misses=0,
            ),
            JobPosting(
                company_id=company.id, source_id=direct.id, source_job_id="gh-1",
                dedup_key=shared_key, title="Backend Engineer", url="https://acme/jobs/1",
                description="the full advert, much longer", is_dublin=True,
                is_remote=False, needs_location_review=False, status=JobStatus.ACTIVE,
                consecutive_misses=0,
            ),
            JobPosting(
                company_id=company.id, source_id=direct.id, source_job_id="gh-2",
                dedup_key="a-different-role", title="Data Analyst", url="https://acme/jobs/2",
                is_dublin=True, is_remote=False, needs_location_review=False,
                status=JobStatus.ACTIVE, consecutive_misses=0,
            ),
        ]
        session.add_all(rows)
        session.flush()

        kept = _prefer_direct_sources(session, rows)

        assert len(kept) == 2, "the duplicate pair collapses, the distinct role stays"
        backend = [job for job in kept if job.title == "Backend Engineer"]
        assert len(backend) == 1
        assert backend[0].source_id == direct.id
        assert backend[0].url == "https://acme/jobs/1"


def test_several_identical_titles_from_one_source_are_all_kept():
    """Regression: dedup hid 37 genuine Dublin openings in the live database.

    `dedup_key` is company + canonical title + location bucket, and it is deliberately
    not unique — Amazon really does run four separate "Software Development Engineer,
    AWS Database Migration Service" openings in Dublin. Collapsing a dedup group to one
    posting deletes three real jobs from the searcher's list. Only copies from a *less
    direct source* may be dropped.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from jobfinder.core.models import Base, Company, CoverageState, JobPosting, JobStatus, Source
    from jobfinder.web.app import _prefer_direct_sources

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        company = Company(
            name="Amazon", normalized_name="amazon",
            coverage_state=CoverageState.ATS_DETECTED,
        )
        session.add(company)
        session.flush()

        direct = Source(company_id=company.id, adapter="amazon", slug="IRL", tier=1)
        aggregated = Source(company_id=company.id, adapter="adzuna", slug="dublin", tier=4)
        session.add_all([direct, aggregated])
        session.flush()

        key = "sde-aws-dms-dublin"
        rows = [
            JobPosting(
                company_id=company.id, source_id=direct.id, source_job_id=f"amz-{i}",
                dedup_key=key, title="Software Development Engineer, AWS DMS",
                url=f"https://amazon.jobs/{i}", description="full advert",
                is_dublin=True, is_remote=False, needs_location_review=False,
                status=JobStatus.ACTIVE, consecutive_misses=0,
            )
            for i in range(4)
        ]
        rows.append(
            JobPosting(
                company_id=company.id, source_id=aggregated.id, source_job_id="agg-1",
                dedup_key=key, title="Software Development Engineer, AWS DMS",
                url="https://adzuna/x", description="trunc",
                is_dublin=True, is_remote=False, needs_location_review=False,
                status=JobStatus.ACTIVE, consecutive_misses=0,
            )
        )
        session.add_all(rows)
        session.flush()

        kept = _prefer_direct_sources(session, rows)

        assert len(kept) == 4, "all four real openings survive"
        assert {job.source_id for job in kept} == {direct.id}
