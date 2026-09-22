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
    assert "<article class=\"record" not in response.text, "must not return results"


def test_no_input_at_all_is_rejected(client):
    response = client.post("/search", data={})
    assert response.status_code == 422
    assert "at least one role" in response.text


def test_submit_button_starts_disabled(client):
    """The requirement is explained before the click, not after it."""
    response = client.get("/")
    assert 'id="find-btn" disabled' in response.text
    assert "Choose at least one tab" in response.text


def test_roles_are_marked_required_in_the_form(client):
    response = client.get("/")
    assert 'stamp--filled">required' in response.text


def test_cv_plus_roles_succeeds(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 200
    assert "records pulled" in response.text


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
    assert "records pulled" in response.text
    # The upload form is still present: it is one page, not a separate results view.
    assert 'id="finder-form"' in response.text


def test_every_record_carries_employer_title_and_date(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
    )
    for part in ("record__employer", "record__title", "accession"):
        assert part in response.text
    assert "btn--apply" in response.text, "every record needs an apply link"


def test_htmx_request_returns_only_the_table(client):
    response = client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        data={"chosen_fields": ["backend"]},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<article class=\"record" in response.text
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
    assert "skills read from your CV" in page_two.text


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
    # The parsed email must not be carried into the session. This assertion used to end
    # in `or True`, which made it pass no matter what - and it was failing, because the
    # 6,000-character CV excerpt the session carried had the address inside it.
    assert "jane@example.com" not in json.dumps(profile).lower()
    # The document does not go into the session at all now, in any length.
    assert not profile.get("text")


def test_the_session_cookie_fits_in_a_browser(client):
    """Regression: with a CV attached the cookie was 8,571 bytes.

    Browsers drop a cookie over about 4KB silently - no error, the next request simply
    arrives without it - so every CV-backed search was running against a session that
    had already been discarded, and paging quietly fell back to a fields-only search.
    """
    import base64

    from itsdangerous import TimestampSigner

    from jobfinder.core.config import settings

    client.post(
        "/search",
        files={"resume": ("cv.txt", CV_BYTES * 40, "text/plain")},
        data={"chosen_fields": ["backend"], "years": "6"},
    )
    cookie = client.cookies.get("session")
    assert cookie and len(cookie) <= 4096, f"cookie is {len(cookie)} bytes"

    payload = json.loads(
        base64.urlsafe_b64decode(TimestampSigner(settings.session_secret).unsign(cookie))
    )
    profile = json.loads(payload["profile"])
    assert len(profile["skills"]) <= 60
    assert len(profile["corpus_terms"]) <= 120


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
    assert "records pulled" in response.text


def _total(response) -> int:
    """Pull the result count out of the rendered heading."""
    import re

    match = re.search(r"([\d,]+) (?:record|internship)", response.text)
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
    assert "graduate and entry-level included" in response.text


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


def test_graduate_only_switch_is_offered(client):
    response = client.get("/")
    assert 'name="graduate_only"' in response.text


def test_graduate_only_switches_the_result_set(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["software-engineering"], "graduate_only": "1", "years": "6"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Found graduate roles or the seasonal empty state - never ordinary roles, and the
    # typed experience must not narrow a graduate search.
    assert "graduate" in response.text.lower()
    assert "records pulled" not in response.text
    assert "up to 6 years" not in response.text


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
    # With no profile the finder shows the slip alone, no filed records.
    assert "<article class=\"record" not in home.text


# ---------------------------------------------------------------------------
# Employer directory
# ---------------------------------------------------------------------------


def test_directory_renders_and_reports_registry_size(client):
    """The directory is the honest answer to "is my employer covered?"."""
    response = client.get("/directory")
    assert response.status_code == 200
    assert "The register" in response.text


def test_directory_pages_rather_than_shipping_the_whole_registry(client):
    """618 employers in one response made a 76,000px page on a phone - worse than the
    results table this redesign existed to fix. The directory pages like results."""
    from jobfinder.web.app import DIRECTORY_PER_PAGE

    first = client.get("/directory")
    assert first.status_code == 200
    assert first.text.count('<article class="record') <= DIRECTORY_PER_PAGE

    second = client.get("/directory", params={"page": 2})
    assert second.status_code == 200
    # A second page must show different employers, not repeat the first.
    assert second.text != first.text

    # Out-of-range pages clamp rather than 404 or render empty.
    assert client.get("/directory", params={"page": 9999}).status_code == 200
    assert client.get("/directory", params={"page": 0}).status_code == 200


def test_directory_counts_do_not_contradict_each_other(client):
    """The lede quotes the registry size and the heading quotes what is shown; when
    those were both stated flatly they read as two different truths on one screen."""
    response = client.get("/directory")
    assert "Showing" in response.text and " of " in response.text


def test_directory_filters(client):
    for show in ("all", "hiring", "linked"):
        response = client.get("/directory", params={"show": show})
        assert response.status_code == 200

    response = client.get("/directory", params={"q": "zzz-no-such-employer"})
    assert response.status_code == 200
    assert "No employer in the register matches" in response.text


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


# ---------------------------------------------------------------------------
# Public deployment hardening
# ---------------------------------------------------------------------------


def test_admin_is_hidden_on_a_public_deployment_without_a_token(client, monkeypatch):
    """The dashboard exposes source slugs and crawl errors, and is expensive to load."""
    from jobfinder.core.config import settings
    from jobfinder.web import app as web_app

    monkeypatch.setattr(web_app, "PUBLIC_DEPLOYMENT", True)
    monkeypatch.setattr(settings, "admin_token", "")

    assert client.get("/admin").status_code == 404


def test_admin_opens_only_with_the_configured_token(client, monkeypatch):
    from jobfinder.core.config import settings
    from jobfinder.web import app as web_app

    monkeypatch.setattr(web_app, "PUBLIC_DEPLOYMENT", True)
    monkeypatch.setattr(settings, "admin_token", "s3cret-token")

    assert client.get("/admin?token=wrong").status_code == 404
    assert client.get("/admin").status_code == 404
    assert client.get("/admin?token=s3cret-token").status_code == 200


def test_a_public_deployment_refuses_to_start_with_the_default_session_secret(tmp_path):
    """A published default secret would let anyone forge a visitor's session cookie."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("JOBFINDER_")}
    env.update(VERCEL="1", JOBFINDER_DATABASE_URL=f"sqlite:///{tmp_path / 'x.db'}")
    refused = subprocess.run(
        [sys.executable, "-c", "import jobfinder.web.app"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert refused.returncode != 0
    assert "JOBFINDER_SESSION_SECRET" in refused.stderr

    env["JOBFINDER_SESSION_SECRET"] = "a-long-random-production-secret"
    started = subprocess.run(
        [sys.executable, "-c", "import jobfinder.web.app"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert started.returncode == 0, started.stderr


def test_one_employer_spelled_three_ways_is_listed_once():
    """Regression: ByrneWallace's graduate advert appeared three times in the results.

    An aggregator carries the employer's name however it was typed into it, so one firm
    arrived as "Byrne Wallace", "Byrne Wallace Shields" and "Byrne Wallace Shields LLP",
    each hashing to a different `dedup_key` and none of them grouping.

    The LLP spelling is no longer reachable here: `normalized_name` is unique and now
    strips the suffix, so that variant lands on the existing company row at ingest. The
    two that survive as separate companies are the ones this pass has to join.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from jobfinder.core.models import Base, Company, CoverageState, JobPosting, JobStatus, Source
    from jobfinder.normalize.dedup import normalize_company_name
    from jobfinder.web.app import _prefer_direct_sources

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        rows = []
        for index, name in enumerate(
            ["Byrne Wallace", "Byrne Wallace Shields"]
        ):
            company = Company(
                name=name,
                normalized_name=normalize_company_name(name),
                coverage_state=CoverageState.UNRESOLVED,
            )
            session.add(company)
            session.flush()
            source = Source(
                company_id=company.id, adapter="adzuna", slug=f"agg-{index}", tier=4
            )
            session.add(source)
            session.flush()
            rows.append(
                JobPosting(
                    company_id=company.id,
                    source_id=source.id,
                    source_job_id=f"j-{index}",
                    # Each was hashed from its own spelling, so no two agree.
                    dedup_key=f"stale-key-{index}",
                    title="Graduate AI Automation Engineer",
                    url=f"https://example.com/{index}",
                    is_dublin=True,
                    is_remote=False,
                    needs_location_review=False,
                    status=JobStatus.ACTIVE,
                    consecutive_misses=0,
                )
            )
        session.add_all(rows)
        session.flush()

        assert len(_prefer_direct_sources(session, rows)) == 1


def test_different_employers_sharing_a_first_word_are_not_merged():
    """The name test must not reach past one employer. Stripping "ireland" leaves
    "bank of", which would otherwise swallow every other bank."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from jobfinder.core.models import Base, Company, CoverageState, JobPosting, JobStatus, Source
    from jobfinder.normalize.dedup import normalize_company_name
    from jobfinder.web.app import _prefer_direct_sources

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        rows = []
        for index, name in enumerate(["Bank of Ireland", "Bank of America"]):
            company = Company(
                name=name,
                normalized_name=normalize_company_name(name),
                coverage_state=CoverageState.UNRESOLVED,
            )
            session.add(company)
            session.flush()
            source = Source(
                company_id=company.id, adapter="greenhouse", slug=f"s-{index}", tier=1
            )
            session.add(source)
            session.flush()
            rows.append(
                JobPosting(
                    company_id=company.id, source_id=source.id, source_job_id=f"j-{index}",
                    dedup_key=f"k-{index}", title="Graduate Analyst",
                    url=f"https://example.com/{index}", is_dublin=True, is_remote=False,
                    needs_location_review=False, status=JobStatus.ACTIVE,
                    consecutive_misses=0,
                )
            )
        session.add_all(rows)
        session.flush()

        assert len(_prefer_direct_sources(session, rows)) == 2


def test_a_legal_suffix_no_longer_makes_a_second_company():
    """The LLP variant used to create a separate company row, and with it a separate
    dedup key and a second copy of every advert."""
    from jobfinder.normalize.dedup import compute_dedup_key, normalize_company_name

    assert normalize_company_name("Byrne Wallace Shields LLP") == normalize_company_name(
        "Byrne Wallace Shields"
    )
    assert compute_dedup_key(
        "Byrne Wallace Shields LLP", "Graduate AI Automation Engineer", is_dublin=True
    ) == compute_dedup_key(
        "Byrne Wallace Shields", "Graduate AI Automation Engineer", is_dublin=True
    )


def test_a_job_its_source_stopped_returning_is_not_offered():
    """Regression: Apply led to "Job not found".

    A posting stays ACTIVE for one grace crawl after it disappears, so a single odd
    response cannot close a live role. Sources are re-crawled about daily, so offering
    that grace period meant adverts stayed on the page for up to two days after the
    employer took them down.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from jobfinder.core.models import Base, Company, CoverageState, JobPosting, JobStatus, Source
    from jobfinder.web.app import _is_offerable

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as session:
        company = Company(
            name="Acme", normalized_name="acme", coverage_state=CoverageState.ATS_DETECTED
        )
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, adapter="ashby", slug="acme", tier=1)
        session.add(source)
        session.flush()

        session.add_all(
            [
                JobPosting(
                    company_id=company.id, source_id=source.id, source_job_id="live",
                    dedup_key="a", title="Graduate Software Engineer",
                    url="https://acme/live", is_dublin=True, is_remote=False,
                    needs_location_review=False, status=JobStatus.ACTIVE,
                    consecutive_misses=0,
                ),
                # Taken down at the source; still ACTIVE, inside its grace crawl.
                JobPosting(
                    company_id=company.id, source_id=source.id, source_job_id="gone",
                    dedup_key="b", title="Graduate Data Engineer",
                    url="https://acme/gone", is_dublin=True, is_remote=False,
                    needs_location_review=False, status=JobStatus.ACTIVE,
                    consecutive_misses=1,
                ),
            ]
        )
        session.flush()

        offered = session.execute(
            select(JobPosting).where(_is_offerable())
        ).scalars().all()

        assert [job.source_job_id for job in offered] == ["live"]

        # The grace period itself is untouched: the row is still ACTIVE, so one sighting
        # puts it straight back on the page rather than needing a re-crawl to recreate.
        gone = session.execute(
            select(JobPosting).where(JobPosting.source_job_id == "gone")
        ).scalar_one()
        assert gone.status is JobStatus.ACTIVE
