"""Web route tests.

Runs against the configured database (SQLite locally), so these assert behaviour that
holds whether or not the database has been crawled.
"""

from __future__ import annotations

import json
import re

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


def test_home_links_the_rest_of_the_companies_to_their_own_page(client):
    """The "and N more companies" link used to expand a list in place; it now goes to
    /companies, where every hiring company is listed."""
    response = client.get("/")
    assert 'href="/companies"' in response.text


def test_companies_page_lists_every_hiring_company(client):
    from jobfinder.core.db import session_scope
    from jobfinder.web.app import _hiring_employers

    with session_scope() as session:
        employers = _hiring_employers(session)

    response = client.get("/companies")
    assert response.status_code == 200
    # One row per hiring company in the full list, whatever the snapshot holds.
    assert response.text.count('class="corow"') == len(employers)
    if employers:
        assert employers[0]["name"].replace("&", "&amp;") in response.text


def test_company_jobs_window_lists_its_jobs(client):
    from jobfinder.core.db import session_scope
    from jobfinder.web.app import _hiring_employers

    with session_scope() as session:
        employers = _hiring_employers(session)
    if not employers:
        pytest.skip("no hiring companies in this database")

    top = employers[0]
    response = client.get(f"/companies/{top['id']}/jobs")
    assert response.status_code == 200
    assert "<!doctype html>" not in response.text.lower(), "a fragment, not a page"
    assert response.text.count("<li>") == top["jobs"]


def test_company_jobs_for_an_unknown_company_is_404(client):
    assert client.get("/companies/999999999/jobs").status_code == 404


def test_logo_domain_prefers_overrides_and_strips_www():
    from jobfinder.web.app import _logo_domain

    assert _logo_domain("Amazon", "https://amazon.jobs") == "amazon.com"
    assert _logo_domain("Stripe", "https://www.stripe.com/") == "stripe.com"
    assert _logo_domain("Uniphar", None) == ""


def test_privacy_page_renders(client):
    response = client.get("/privacy")
    assert response.status_code == 200
    text = response.text.lower()
    # The CV file is kept now, for tailoring; the page must say so, and say what else sees it.
    assert "never stored" not in text
    assert "only you can read it" in text
    assert "gemini" in text and "languagetool" in text


def test_old_results_url_still_redirects_home(client):
    """The separate results page was folded into the finder; old links must not 404."""
    response = client.get("/results", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_the_finder_no_longer_takes_a_cv(client):
    """The CV goes up once, on the profile. The finder has no file input, and a file
    posted to the search anyway is not read: roles are still what a search needs."""
    assert 'type="file"' not in client.get("/").text
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
    assert "Pick at least one above" in response.text


def test_roles_are_marked_required_in_the_form(client):
    response = client.get("/")
    assert 'stamp--filled">required' in response.text


def test_roles_alone_succeed(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 200
    assert "jobs match" in response.text


def test_paging_does_not_re_trigger_the_roles_requirement(client):
    """Page two posts without the boxes, so the session must satisfy the check."""
    client.post(
        "/search",
        data={"chosen_fields": ["backend"]},
    )
    page_two = client.post(
        "/search?page=2", data={}, headers={"HX-Request": "true"}
    )
    assert page_two.status_code == 200


def test_search_renders_results_on_the_same_page(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend"]},
    )
    assert response.status_code == 200
    assert "jobs match" in response.text
    # The search form is still present: it is one page, not a separate results view.
    assert 'id="finder-form"' in response.text


def test_every_record_carries_employer_title_and_date(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend"]},
    )
    for part in ("record__employer", "record__title", "record__age"):
        assert part in response.text
    assert "btn--apply" in response.text, "every record needs an apply link"


def test_the_filter_bar_narrows_by_company_and_field(client):
    """The tabs and the company picker narrow a search without re-ranking it."""
    fields = {"chosen_fields": ["backend", "cloud", "devops"]}
    everything = client.post("/search", data=fields, headers={"HX-Request": "true"}).text
    companies = re.findall(r'<option value="([^"]+)"[^>]*>[^<]*\((\d+)\)</option>', everything)
    assert companies, "the company picker lists who is hiring"
    name, count = companies[0]

    narrowed = client.post(
        "/search", data={**fields, "company": name}, headers={"HX-Request": "true"}
    ).text
    assert _total(type("R", (), {"text": narrowed})) == int(count)
    employers = set(re.findall(r'class="record__employer">([^<]+)<', narrowed))
    assert employers == {name.replace("&", "&amp;")}
    assert "Show all" in narrowed

    tabs = re.findall(r'name="facet" value="([a-z_]+)"', everything)
    assert tabs, "a search over several fields offers a tab per field"
    one = client.post(
        "/search", data={**fields, "facet": tabs[0]}, headers={"HX-Request": "true"}
    ).text
    assert _total(type("R", (), {"text": one})) < _total(type("R", (), {"text": everything}))

    # Nonsense filters are ignored rather than emptying the list.
    ignored = client.post(
        "/search", data={**fields, "facet": "nope", "company": "Nobody Ltd"},
        headers={"HX-Request": "true"},
    ).text
    assert _total(type("R", (), {"text": ignored})) == _total(type("R", (), {"text": everything}))


def test_every_picked_field_gets_a_tab_and_the_tabs_add_up_to_all(client):
    """A picked field with nothing open used to vanish from the bar, which read as
    though the choice had been ignored, and rows in no field had no tab at all."""
    fields = {"chosen_fields": ["machine-learning", "data-science"], "graduate_only": "1"}
    text = client.post("/search", data=fields, headers={"HX-Request": "true"}).text
    tabs = dict(re.findall(
        r'name="facet" value="([a-z-]+)"[^>]*>\s*<span class="facet__face">[^<]*<b>([\d,]+)</b>',
        text,
    ))
    if not tabs:
        pytest.skip("this snapshot has no graduate jobs to tab")
    assert {"machine-learning", "data-science"} <= set(tabs)
    everything = int(re.search(r'All <b>([\d,]+)</b>', text).group(1).replace(",", ""))
    assert sum(int(n.replace(",", "")) for n in tabs.values()) == everything
    for key, count in tabs.items():
        if count == "0":
            assert re.search(rf'value="{key}"[^>]*disabled', text), "an empty tab cannot be picked"


def test_the_company_view_lays_the_same_results_out_by_employer(client):
    fields = {"chosen_fields": ["backend", "cloud", "devops"]}
    as_list = client.post("/search", data=fields, headers={"HX-Request": "true"}).text
    by_company = client.post(
        "/search", data={**fields, "view": "company"}, headers={"HX-Request": "true"}
    ).text
    assert 'class="cocard' in by_company and 'class="record' not in by_company
    # Same search, same total: the view only changes the layout.
    assert _total(type("R", (), {"text": by_company})) == _total(type("R", (), {"text": as_list}))
    names = re.findall(r'class="cocard__name">([^<]+)<', by_company)
    assert len(names) == len(set(names)), "one card per employer"
    # An unknown view falls back to the list rather than erroring.
    odd = client.post("/search", data={**fields, "view": "grid"}, headers={"HX-Request": "true"}).text
    assert 'class="record' in odd


def test_htmx_request_returns_only_the_table(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend"]},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "<article class=\"record" in response.text
    # A partial swap must not re-send the whole document.
    assert "<!doctype html>" not in response.text.lower()
    assert 'id="finder-form"' not in response.text


def test_sort_and_paging_controls_do_not_resend_a_file_input(client):
    """The sort select and paging links sit outside the form, so htmx url-encodes them.
    Including a file input sent "[object File]", the server answered 422, and htmx
    silently dropped the response - both controls appeared dead. The form has no file
    input now, and the exclusion stays so one cannot bring the bug back."""
    response = client.post(
        "/search",
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


def test_the_session_holds_only_what_was_typed_and_ticked(client):
    """The CV's reading lives on the profile now, not in the session cookie. What the
    session carries is the form, which is small whatever CV is on file."""
    import base64

    from itsdangerous import TimestampSigner

    from jobfinder.core.config import settings

    client.post("/search", data={"chosen_fields": ["backend"], "years": "6"})
    cookie = client.cookies.get("session")
    assert cookie and len(cookie) <= 1024, f"cookie is {len(cookie)} bytes"
    payload = json.loads(
        base64.urlsafe_b64decode(TimestampSigner(settings.session_secret).unsign(cookie))
    )
    profile = json.loads(payload["profile"])
    assert profile["fields"] == ["backend"]
    for key in ("skills", "corpus_terms", "seniority", "cv_summary", "titles"):
        assert key not in profile


def test_search_without_a_cv_still_works(client):
    """Fields alone are a valid search - a CV is optional."""
    response = client.post("/search", data={"chosen_fields": ["backend"]})
    assert response.status_code == 200
    assert "jobs match" in response.text


def _total(response) -> int:
    """Pull the result count out of the rendered heading."""
    import re

    match = re.search(r"([\d,]+) (?:job|internship)", response.text)
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
    assert "graduate and entry-level jobs included" in response.text


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
    assert "jobs match" not in response.text
    assert "asking 6 years or less" not in response.text


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


# ------------------------------------------------------------- experience


def test_years_is_a_slider_from_any_to_fifteen_plus(client):
    page = client.get("/").text
    slider = re.search(r'<input type="range" id="years" name="years"[^>]*>', page)
    assert slider, "years of experience should be a slider"
    assert 'min="-1"' in slider.group(0) and 'max="16"' in slider.group(0)
    assert 'value="-1"' in slider.group(0), "a fresh visit starts on Any"


def test_any_on_the_slider_means_every_level(client):
    """Any is sent as -1; it must show every opening, not be read as 0 years."""
    any_total = client.post("/search", data={"chosen_fields": ["backend"], "years": "-1"}).text
    blank_total = client.post("/search", data={"chosen_fields": ["backend"], "years": ""}).text
    grab = lambda t: re.search(r"results: ([\d,]+) found", t).group(1)
    assert grab(any_total) == grab(blank_total)
    assert "or less" not in any_total.split("results:")[1].split("</p>")[0]


def test_paging_sends_every_field_in_the_form(client):
    """Regression: when years became a list, paging stopped sending it, so page two of
    a "0 years" search became a search of every level (81 jobs, then 244)."""
    page = client.post("/search", data={"chosen_fields": ["backend"], "years": "0"}).text
    includes = re.findall(r'hx-include="([^"]+)"', page)
    assert includes and all("#finder-form select" in i and "#finder-form input" in i for i in includes)


def test_fifteen_plus_asks_for_every_role(client):
    for sent in ("16", "15+"):
        page = client.post("/search", data={"chosen_fields": ["backend"], "years": sent}).text
        assert "15+ years of experience" in page


# ------------------------------------------------------------------ copy


def test_no_page_carries_an_em_dash(client):
    for path in ("/", "/companies", "/privacy"):
        text = client.get(path).text
        assert "\u2014" not in text and "&mdash;" not in text, path


def test_employer_dashes_become_commas_not_hyphens():
    from markupsafe import Markup

    from jobfinder.web.app import _no_em_dashes

    assert _no_em_dashes("Engineer \u2014 Payments") == "Engineer, Payments"
    assert _no_em_dashes("Engineer \u2013 Dublin") == "Engineer, Dublin"
    assert _no_em_dashes("3\u20135 years") == "3\u20135 years"
    safe = _no_em_dashes(Markup("<b>a \u2014 b</b>"))
    assert isinstance(safe, Markup) and str(safe) == "<b>a, b</b>"


# ----------------------------------------------------------------- speed


def test_htmx_is_served_by_the_site_and_cached(client):
    response = client.get("/static/htmx-1.9.12.min.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert client.get("/static/..%2Fapp.py").status_code == 404
    assert "unpkg.com" not in client.get("/").text


def test_precomputed_skills_from_other_code_are_ignored():
    from jobfinder.matching import rank

    assert rank.preload_skills("not-this-code", [("k", ["python"])]) == 0
    assert "k" not in rank._PRECOMPUTED_SKILLS


def test_precomputed_skills_are_what_ranking_would_have_found():
    from jobfinder.matching import rank
    from jobfinder.normalize.taxonomy import extract_skills

    text = "Backend Engineer\nPython, Kafka and Kubernetes on AWS."
    rank._ADVERTS.pop(text, None)
    rank.preload_skills(rank.skills_fingerprint(), [(rank.advert_hash(text), ["sentinel"])])
    try:
        assert rank._advert(text).skills == frozenset({"sentinel"})
    finally:
        rank._PRECOMPUTED_SKILLS.clear()
        rank._ADVERTS.pop(text, None)
    assert rank._advert(text).skills == frozenset(extract_skills(text))


def test_an_early_career_search_hides_no_graduate_job(client):
    """Only a dozen or so graduate jobs are open at a time, and a programme usually takes
    any discipline, so ticking a field orders them rather than hiding most of them."""
    totals = {
        field: _total(client.post("/search", data={"chosen_fields": [field], "graduate_only": "1"}))
        for field in ("data-science", "pharma", "accounting")
    }
    assert len(set(totals.values())) == 1, totals
    page = client.post("/search", data={"chosen_fields": ["pharma"], "graduate_only": "1"}).text
    assert "in or near the fields you picked" in page


# ------------------------------------------------------------------ show more


def _record_titles(html: str) -> list[str]:
    return re.findall(r'<h3 class="record__title">\s*<a [^>]*>([^<]+)</a>', html)


def test_show_more_returns_the_next_rows_alone(client):
    """The pager is gone: the rows end with Show more, which fetches the next page and
    answers with those rows only, so nothing already on screen is sent again."""
    first = client.post(
        "/search", data={"chosen_fields": ["backend", "frontend", "data-engineering"]},
        headers={"HX-Request": "true"},
    )
    if 'class="more"' not in first.text:
        pytest.skip("one page of results in this database")
    assert "Page 1 of" not in first.text
    assert 'hx-trigger="click, intersect once"' in first.text

    more = client.post(
        "/search?page=2",
        data={"chosen_fields": ["backend", "frontend", "data-engineering"], "more": "1"},
        headers={"HX-Request": "true"},
    )
    assert more.status_code == 200
    assert 'class="record' in more.text
    # Rows only: no heading, filter bar or window around them.
    assert "resultswin" not in more.text and 'class="rbar"' not in more.text
    assert _record_titles(more.text) and _record_titles(more.text) != _record_titles(first.text)


def test_a_band_heading_is_not_repeated_on_the_next_page():
    """A page fetched by Show more opens a heading only where the band changes, so the
    band the last page ended in does not get a second heading."""
    from jobfinder.web.app import templates

    item = {
        "tier": 0, "company": "Acme", "domain": "", "age": "", "posted": None, "place": "",
        "experience": None, "field": None, "skills": [], "is_new": False, "stretch": False,
        "also": [], "strength": 3, "strength_label": "Strong match", "why": "",
        "advert_key": "k", "applied": False, "saved": False,
        "job": {"id": 1, "title": "Engineer", "url": "https://example.com", "is_remote": False},
    }
    context = dict(
        view="list", items=[item], tiered=True, band_counts={0: 30, 1: 4},
        tier_bands={0: "In the fields you picked", 1: "Where your CV points"},
        page=2, pages=2, show_field=False, accounts_enabled=False, account=None,
    )
    continued = templates.get_template("_result_rows.html").render(prev_tier=0, **context)
    assert "In the fields you picked" not in continued
    changed = templates.get_template("_result_rows.html").render(prev_tier=None, **context)
    assert "In the fields you picked" in changed


def test_the_filters_wait_for_show_jobs_on_a_phone(client):
    """On a phone the filter bar is a sheet: it re-runs on change only when the sheet
    layout is not holding it, and Show jobs fires `apply` to run it with every choice."""
    page = client.post(
        "/search", data={"chosen_fields": ["backend"]}, headers={"HX-Request": "true"}
    ).text
    assert 'hx-trigger="change[!rbarHeld], apply"' in page
    assert "data-filters-apply" in page and "data-filters-open" in page
    # Sort sits on the bar that stays, once, so paging never sends two orders.
    assert page.count('name="sort"') == 1


# ------------------------------------------------------------------ one advert


def _a_live_job_id() -> int | None:
    from sqlalchemy import select

    from jobfinder.core.db import session_scope
    from jobfinder.core.models import JobPosting
    from jobfinder.web.app import _is_offerable

    with session_scope() as session:
        return session.scalar(
            select(JobPosting.id).where(_is_offerable(), JobPosting.is_dublin.is_(True),
                                        JobPosting.description.is_not(None)).limit(1)
        )


def test_a_job_opens_as_a_panel_for_htmx_and_a_page_otherwise(client):
    job_id = _a_live_job_id()
    if job_id is None:
        pytest.skip("no live job in this database")
    panel = client.get(f"/jobs/{job_id}", headers={"HX-Request": "true"})
    assert panel.status_code == 200
    assert 'class="jobx"' in panel.text and "<!doctype html>" not in panel.text.lower()
    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    assert "<!doctype html>" in page.text.lower() and 'class="jobx"' in page.text
    assert "Apply on" in page.text


def test_a_closed_or_unknown_job_says_so(client):
    assert client.get("/jobs/999999999").status_code == 404
    assert "has closed" in client.get("/jobs/999999999").text


def test_a_row_title_leads_to_the_job_on_this_site(client):
    """The row opens the advert here; Apply is what goes to the employer."""
    page = client.post("/search", data={"chosen_fields": ["backend"]},
                       headers={"HX-Request": "true"}).text
    titles = re.findall(r'<h3 class="record__title">\s*<a href="([^"]+)"', page)
    assert titles and all(t.startswith("/jobs/") for t in titles)


# ------------------------------------------------------------------ the address bar


def test_a_search_writes_itself_into_the_address(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend", "frontend"], "years": "2", "sort": "newest",
              "include_remote": "1"},
        headers={"HX-Request": "true"},
    )
    url = response.headers["HX-Push-Url"]
    assert url.startswith("/?f=backend&f=frontend")
    assert "y=2" in url and "sort=newest" in url and "remote=1" in url
    # Show more is the same search, so it leaves the address alone.
    more = client.post("/search?page=2", data={"chosen_fields": ["backend"], "more": "1"},
                       headers={"HX-Request": "true"})
    assert "HX-Push-Url" not in more.headers


def test_a_search_link_runs_the_search(client):
    """Refresh, Back and a shared link all open the address the search wrote."""
    page = client.get("/?f=backend&y=2&sort=newest")
    assert page.status_code == 200
    assert "<div id=\"results\" data-landing>" in page.text and 'class="win resultswin"' in page.text
    assert '<option value="newest" selected>' in page.text
    # The form shows the search it ran.
    assert re.search(r'value="backend"\s+checked', page.text)


def test_a_plain_visit_is_still_blank(client):
    client.post("/search", data={"chosen_fields": ["backend"]})
    page = client.get("/")
    assert "<div id=\"results\" data-landing>" not in page.text and 'class="win resultswin"' not in page.text


def test_the_address_leaves_out_what_the_search_ignored(client):
    response = client.post(
        "/search",
        data={"chosen_fields": ["backend"], "sort": "bogus", "company": "No Such Employer Ltd"},
        headers={"HX-Request": "true"},
    )
    url = response.headers["HX-Push-Url"]
    assert "sort=" not in url and "co=" not in url


# ------------------------------------------------------------------ installable


def test_the_site_is_installable(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    data = manifest.json()
    assert data["display"] == "standalone" and data["start_url"].startswith("/")
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    home = client.get("/").text
    assert 'rel="manifest"' in home and "serviceWorker" in home


def test_the_service_worker_is_served_from_the_root_and_never_pinned(client):
    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert "javascript" in worker.headers["content-type"]
    assert worker.headers["cache-control"] == "no-cache"
    # It keeps pages only as a fallback, and never touches a POST.
    assert "request.method !== 'GET'" in worker.text


def test_the_offline_page_is_not_personal(client):
    page = client.get("/offline")
    assert page.status_code == 200 and "kept-on-this-device" in page.text


def test_the_keyboard_shortcuts_are_listed(client):
    page = client.get("/?f=backend").text
    assert 'id="keys"' in page and "data-keys-open" in page
    for key in ("<kbd>j</kbd>", "<kbd>s</kbd>", "<kbd>a</kbd>", "<kbd>/</kbd>"):
        assert key in page


# ------------------------------------------------------------------ what adverts state


def test_the_work_mode_picker_narrows_to_what_adverts_state(client):
    from jobfinder.web.app import _facts_for, JobPosting  # noqa: F401

    everything = client.post("/search", data={"chosen_fields": ["software-engineering"]},
                             headers={"HX-Request": "true"})
    hybrid = client.post("/search", data={"chosen_fields": ["software-engineering"], "mode": "hybrid"},
                         headers={"HX-Request": "true"})
    total = lambda r: int(re.search(r'class="rstick__count">\s*<b>([\d,]+)</b>', r.text).group(1).replace(",", ""))
    assert total(hybrid) <= total(everything)
    assert hybrid.headers["HX-Push-Url"].endswith("mode=hybrid")
    rows = re.findall(r'<article class="record.*?</article>', hybrid.text, re.S)
    assert all("hybrid" in row for row in rows)
    # An unknown mode is ignored rather than emptying the list.
    bogus = client.post("/search", data={"chosen_fields": ["software-engineering"], "mode": "moon"},
                        headers={"HX-Request": "true"})
    assert total(bogus) == total(everything)


def test_states_a_salary_keeps_only_adverts_that_give_one(client):
    paid = client.post("/search", data={"chosen_fields": ["software-engineering"], "paid": "1"},
                       headers={"HX-Request": "true"})
    rows = re.findall(r'<article class="record.*?</article>', paid.text, re.S)
    assert all("chip--pay" in row for row in rows)
    assert "paid=1" in paid.headers["HX-Push-Url"]


def test_the_job_panel_says_when_pay_is_not_stated(client):
    from jobfinder.normalize.facts import salary

    job_id = _a_live_job_id()
    if job_id is None:
        pytest.skip("no live job in this database")
    panel = client.get(f"/jobs/{job_id}", headers={"HX-Request": "true"}).text
    assert "<dt>Salary</dt>" in panel and "<dt>Work mode</dt>" in panel
