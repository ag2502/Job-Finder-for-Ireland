"""Accounts, and the record of what a searcher has applied to.

Supabase is stubbed. These tests are about what the finder does with an account - that a
job applied to leaves the results, that the toggle brings it back, that an outage fails
open - none of which needs a real network call to prove, and all of which would become
untestable if it did.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from jobfinder.core import supabase
from jobfinder.core.db import init_db
from jobfinder.web.app import app


def _account(email: str = "jane@example.com") -> supabase.Account:
    return supabase.Account(
        user_id="user-1",
        email=email,
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
    )


class FakeSupabase:
    """Stands in for the project: one account, and the rows it has written."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.saved_rows: list[dict] = []
        self.profile: dict | None = None
        self.fail_with: Exception | None = None
        self.profile_fails_with: Exception | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
        monkeypatch.setattr(supabase, "configured", lambda: True)
        monkeypatch.setattr(supabase, "sign_in", lambda email, password: _account(email))
        monkeypatch.setattr(supabase, "sign_up", lambda email, password: _account(email))
        monkeypatch.setattr(supabase, "sign_out", lambda account: None)
        monkeypatch.setattr(supabase, "list_applications", self._list)
        monkeypatch.setattr(supabase, "add_application", self._add)
        monkeypatch.setattr(supabase, "remove_application", self._remove)
        monkeypatch.setattr(supabase, "list_saved", self._list_saved)
        monkeypatch.setattr(supabase, "add_saved", self._add_saved)
        monkeypatch.setattr(supabase, "remove_saved", self._remove_saved)
        monkeypatch.setattr(supabase, "get_profile", self._get_profile)
        monkeypatch.setattr(supabase, "save_profile", self._save_profile)
        monkeypatch.setattr(supabase, "providers", lambda: {"google": True})
        monkeypatch.setattr(supabase.settings, "supabase_url", "https://proj.supabase.co")
        return self

    def _list(self, account):
        if self.fail_with:
            raise self.fail_with
        return list(self.rows)

    def _add(self, account, *, advert_key, title, company, url):
        if self.fail_with:
            raise self.fail_with
        self.rows = [r for r in self.rows if r["advert_key"] != advert_key]
        self.rows.append(
            {
                "advert_key": advert_key,
                "title": title,
                "company": company,
                "url": url,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _remove(self, account, advert_key):
        if self.fail_with:
            raise self.fail_with
        self.rows = [r for r in self.rows if r["advert_key"] != advert_key]

    def _list_saved(self, account):
        if self.fail_with:
            raise self.fail_with
        return list(self.saved_rows)

    def _add_saved(self, account, *, advert_key, title, company, url):
        if self.fail_with:
            raise self.fail_with
        self.saved_rows = [
            r for r in self.saved_rows if r["advert_key"] != advert_key
        ]
        self.saved_rows.append(
            {
                "advert_key": advert_key,
                "title": title,
                "company": company,
                "url": url,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _remove_saved(self, account, advert_key):
        if self.fail_with:
            raise self.fail_with
        self.saved_rows = [
            r for r in self.saved_rows if r["advert_key"] != advert_key
        ]

    def _get_profile(self, account):
        if self.profile_fails_with:
            raise self.profile_fails_with
        return dict(self.profile) if self.profile is not None else None

    def _save_profile(self, account, **columns):
        if self.profile_fails_with:
            raise self.profile_fails_with
        # Only the columns named change, as with the real upsert.
        self.profile = {**(self.profile or {}), **columns}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
    return FakeSupabase().install(monkeypatch)


@pytest.fixture
def client(fake: FakeSupabase) -> TestClient:
    init_db()
    with TestClient(app) as c:
        yield c


def _signed_in(client: TestClient) -> TestClient:
    response = client.post(
        "/login", data={"email": "jane@example.com", "password": "hunter2hunter2"}
    )
    assert response.status_code == 200
    return client


def _first_result(client: TestClient, **extra) -> tuple[str, str]:
    """Run a search and return the first row's advert key and title."""
    import re

    data = {"chosen_fields": ["software-engineering"], **extra}
    page = client.post("/search", data=data).text
    key = re.search(r'"advert_key": "([0-9a-f]{32})"', page)
    title = re.search(r'<h3 class="record__title">\s*<a[^>]*>([^<]+)</a>', page)
    assert key and title, "expected at least one result with an advert key"
    return key.group(1), title.group(1).strip()


# ------------------------------------------------------------------ sign-in


def test_sign_in_then_out(client: TestClient):
    home = client.get("/").text
    assert "Sign in" in home and ">Applied<" not in home

    _signed_in(client)
    home = client.get("/").text
    assert ">Applied<" in home
    assert "jane@example.com" in home

    client.post("/logout")
    assert "Sign in" in client.get("/").text


def test_a_short_password_is_refused_before_it_reaches_supabase(client: TestClient):
    response = client.post("/signup", data={"email": "a@b.co", "password": "short"})
    assert response.status_code == 422
    assert "at least 8 characters" in response.text


def test_sign_in_is_not_offered_when_no_project_is_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    """A checkout with no secrets still searches; it simply has no accounts."""
    monkeypatch.setattr(supabase, "configured", lambda: False)
    init_db()
    with TestClient(app) as c:
        home = c.get("/")
        assert home.status_code == 200
        assert "Sign in" not in home.text
        assert c.post("/login", data={"email": "a@b.co", "password": "x" * 8}).status_code == 404


# -------------------------------------------------------------- applications


def test_applying_removes_the_job_from_later_searches(client: TestClient, fake):
    """The whole point: days later there is no question of whether you applied."""
    _signed_in(client)
    key, title = _first_result(client)

    marked = client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    assert marked.status_code == 200
    assert "applied" in marked.text and "Undo" in marked.text

    page = client.post("/search", data={"chosen_fields": ["software-engineering"]}).text
    assert key not in page, "the applied job must not come back"


def test_the_toggle_brings_applied_jobs_back(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )

    hidden = client.post("/search", data={"chosen_fields": ["software-engineering"]}).text
    assert key not in hidden

    shown = client.post(
        "/search", data={"chosen_fields": ["software-engineering"], "show_applied": "1"}
    ).text
    assert key in shown


def test_undo_puts_a_job_back(client: TestClient, fake):
    """A mis-click must not hide a job permanently."""
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    assert key not in client.post("/search", data={"chosen_fields": ["software-engineering"]}).text

    client.post("/applications/remove", data={"advert_key": key})
    assert key in client.post("/search", data={"chosen_fields": ["software-engineering"]}).text


def test_marking_the_same_advert_twice_is_harmless(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    for _ in range(3):
        response = client.post(
            "/applications",
            data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
        )
        assert response.status_code == 200
    assert len(fake.rows) == 1


def test_applications_page_lists_what_was_applied_to(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )

    page = client.get("/applications")
    assert page.status_code == 200
    assert title in page.text
    assert "Acme" in page.text


def test_applications_page_asks_a_stranger_to_sign_in(client: TestClient):
    page = client.get("/applications", follow_redirects=False)
    assert page.status_code == 303
    # ...and remembers where they were going, so signing in lands them back on it.
    assert page.headers["location"] == "/login?next=/applications"


def test_history_is_kept_even_after_the_job_closes(client: TestClient, fake):
    """The snapshot only carries open roles, so a foreign key would leave the page
    blank days after applying. The record stands on its own."""
    _signed_in(client)
    fake.rows.append(
        {
            "advert_key": "f" * 32,
            "title": "Graduate Software Engineer",
            "company": "Wayflyer",
            "url": "https://jobs.ashbyhq.com/wayflyer/gone",
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    page = client.get("/applications").text
    assert "Graduate Software Engineer" in page
    assert "Wayflyer" in page


# ------------------------------------------------------------------ failure


def test_an_outage_shows_every_job_rather_than_none(client: TestClient, fake):
    """Failing open is the right way round.

    The worst case is that a job already applied to appears in the list - exactly the
    behaviour before accounts existed. Failing closed would empty the page.
    """
    _signed_in(client)
    key, _ = _first_result(client)
    fake.fail_with = httpx.ConnectError("supabase unreachable")

    response = client.post("/search", data={"chosen_fields": ["software-engineering"]})
    assert response.status_code == 200
    assert key in response.text


def test_a_failed_mark_says_so_instead_of_claiming_success(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    fake.fail_with = supabase.SupabaseError("permission denied")

    response = client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    assert response.status_code == 502
    assert "could not save" in response.text


def test_marking_requires_an_account(client: TestClient):
    """A POST gets a status, not a redirect. htmx would follow a redirect and swap a
    whole sign-in page into the Apply button that made the request."""
    response = client.post(
        "/applications",
        data={"advert_key": "a" * 32, "title": "x", "company": "y", "url": "z"},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_an_expired_token_is_refreshed_rather_than_signing_someone_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _signed_in(client)
    stale = _account()
    stale.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
    assert stale.expired

    calls: list[str] = []

    def _refresh(account):
        calls.append(account.refresh_token)
        return _account()

    monkeypatch.setattr(supabase, "refresh", _refresh)
    monkeypatch.setattr(supabase.Account, "from_session", classmethod(lambda cls, d: stale if d else None))

    page = client.get("/")
    assert page.status_code == 200
    # Once, not twice. The gate and the page context both want the account, and Supabase
    # rotates refresh tokens - a second refresh would present a spent one and sign the
    # searcher out in the middle of a request that was working.
    assert calls == ["refresh-token"]


def test_a_dead_refresh_token_signs_the_person_out_cleanly(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _signed_in(client)
    stale = _account()
    stale.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()

    monkeypatch.setattr(supabase.Account, "from_session", classmethod(lambda cls, d: stale if d else None))
    monkeypatch.setattr(
        supabase, "refresh", lambda account: (_ for _ in ()).throw(supabase.SupabaseError("expired"))
    )

    page = client.get("/")
    assert page.status_code == 200
    assert "Sign in" in page.text


# --------------------------------------------------------------- wire format
#
# These assert what actually goes out on the wire. The stub above is faithful to how
# Supabase *behaves*, which is exactly why it missed a real bug: marking the same advert
# twice worked against the fake and failed against the live project, because PostgREST
# needs to be told which constraint counts as a duplicate. A fake cannot catch that; only
# looking at the request can.


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Record outgoing requests and answer them with a bare success."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/applications") and request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={})

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(supabase.settings, "supabase_url", "https://proj.supabase.co")
    monkeypatch.setattr(supabase.settings, "supabase_anon_key", "sb_publishable_abc")
    monkeypatch.setattr(httpx, "Client", fake_client)
    return seen


def test_marking_names_the_constraint_that_counts_as_a_duplicate(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: a second Apply click raised a unique-constraint error.

    `resolution=merge-duplicates` asks for an upsert, but PostgREST infers the conflict
    target from the primary key - `id`, a fresh uuid every insert, which never conflicts
    - so the unique constraint on (user_id, advert_key) raised instead of merging.
    """
    seen = _capture(monkeypatch)
    supabase.add_application(
        _account(), advert_key="a" * 32, title="t", company="c", url="u"
    )

    request = seen[-1]
    assert request.url.params["on_conflict"] == "user_id,advert_key"
    assert "merge-duplicates" in request.headers["Prefer"]


def test_the_cv_is_read_and_written_as_its_owner_only(monkeypatch: pytest.MonkeyPatch):
    """The CV reading lives in the account and nowhere else. Every call carries the
    person's own token, so row level security decides what exists, and names their own
    row; the service key, which would bypass that, is never used."""
    seen = _capture(monkeypatch)
    supabase.get_profile(_account())
    supabase.save_profile(_account(), cv={"skills": ["python"]})

    read, write = seen[-2], seen[-1]
    for request in (read, write):
        assert request.headers["authorization"] == "Bearer access-token"
        assert request.url.path == "/rest/v1/profiles"
    assert read.url.params["user_id"] == "eq.user-1"
    assert json.loads(write.content)["user_id"] == "user-1"
    assert write.url.params["on_conflict"] == "user_id"
    assert "merge-duplicates" in write.headers["Prefer"]


def test_the_database_keeps_each_profile_to_its_owner():
    """What actually stops one account reading another's CV: the table's own policies,
    enforced by Postgres whatever the application code does."""
    from pathlib import Path

    schema = (Path(__file__).parent.parent / "schema.sql").read_text()
    table = schema[schema.index("create table if not exists public.profiles"):]
    assert "alter table public.profiles enable row level security;" in table
    for action in ("select", "insert", "update", "delete"):
        policy = re.search(
            rf"on public\.profiles for {action}\s+(.*?);", table, re.S
        )
        assert policy, f"no {action} policy on profiles"
        assert "auth.uid() = user_id" in policy.group(1)


def test_a_publishable_key_is_not_sent_as_a_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
):
    """`sb_publishable_...` keys are opaque handles, not credentials. Presenting one as
    a bearer token is rejected; it belongs in `apikey` alone."""
    seen = _capture(monkeypatch)
    supabase.sign_in("jane@example.com", "hunter2hunter2")

    request = seen[-1]
    assert request.headers["apikey"] == "sb_publishable_abc"
    assert "authorization" not in request.headers


def test_a_legacy_anon_key_is_still_sent_as_a_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
):
    """The older `anon` key is itself a JWT, and sending it is what tells PostgREST to
    act as the anon role."""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(supabase.settings, "supabase_anon_key", "eyJhbGciOiJIUzI1NiJ9.x.y")
    supabase.sign_in("jane@example.com", "hunter2hunter2")

    assert seen[-1].headers["authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.x.y"


def test_a_signed_in_call_carries_the_persons_own_token(monkeypatch: pytest.MonkeyPatch):
    """Row level security reads this header. Sending the project key instead would make
    every request anonymous, and the searcher would see an empty history."""
    seen = _capture(monkeypatch)
    supabase.list_applications(_account())

    assert seen[-1].headers["authorization"] == "Bearer access-token"


def test_the_data_api_endpoint_is_accepted_where_the_project_url_is_wanted():
    """The dashboard shows `.../rest/v1/` more prominently than the bare project URL,
    so that is what gets pasted. Appending to it would give `/rest/v1/rest/v1/...`."""
    from jobfinder.core.config import Settings

    for pasted in (
        "https://proj.supabase.co/rest/v1/",
        "https://proj.supabase.co/rest/v1",
        "https://proj.supabase.co/",
        "  https://proj.supabase.co  ",
    ):
        assert Settings(supabase_url=pasted).supabase_url == "https://proj.supabase.co"


# ------------------------------------------------------------- the login gate


def test_a_signed_out_visitor_can_browse_every_job(client: TestClient):
    """The site is open. It was gated everywhere until launch, which meant anyone
    arriving from a link saw a sign-in wall instead of a single job - the wrong first
    impression for a product whose pitch is that every opening is in one place."""
    for path in ("/", "/privacy"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path

    # Searching works signed out too; only the account's own pages are behind the gate.
    results = client.post("/search", data={"chosen_fields": ["backend"]})
    assert results.status_code == 200


def test_only_the_account_pages_are_gated(client: TestClient):
    for path in ("/applications", "/saved"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == f"/login?next={path}"


def test_signing_in_opens_the_site(client: TestClient):
    _signed_in(client)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Every job in Dublin" in response.text


@pytest.mark.parametrize("path", ["/login", "/signup", "/privacy"])
def test_the_pages_needed_before_signing_up_stay_public(client: TestClient, path: str):
    """Privacy especially: someone must be able to read what an account will store
    about them before being asked to create one."""
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200


def test_the_sign_in_form_does_not_exist_when_accounts_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    """Both POST handlers already 404 without Supabase and the header hides the link,
    but the GET handlers still rendered a full form. Anyone arriving by bookmark or
    typed URL got a page that looked functional and 404'd on submit."""
    monkeypatch.setattr(supabase, "configured", lambda: False)
    init_db()
    with TestClient(app) as c:
        assert c.get("/login").status_code == 404
        assert c.get("/signup").status_code == 404
        # The job search is untouched by accounts being off.
        assert c.get("/").status_code == 200


def test_the_gate_cannot_lock_everyone_out_when_accounts_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    """The failure this guard exists to prevent.

    With no accounts service there is no `/login` to redirect to, so gating would send
    every visitor to a 404. Environment variables going missing should cost the sign-in
    button, not the job search.
    """
    monkeypatch.setattr(supabase, "configured", lambda: False)
    init_db()
    with TestClient(app) as c:
        response = c.get("/", follow_redirects=False)
        assert response.status_code == 200
        assert "Every job in Dublin" in response.text


# --------------------------------------------------------------- saved jobs


def test_saving_a_job_keeps_it_in_the_results(client: TestClient, fake):
    """The opposite of applying, on purpose. An applied job leaves the drawer because
    the decision is made; a saved one is still being weighed up, so hiding it would
    defeat the point of saving it."""
    _signed_in(client)
    key, title = _first_result(client)

    marked = client.post(
        "/saved",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    assert marked.status_code == 200
    assert "Saved" in marked.text
    assert [r["advert_key"] for r in fake.saved_rows] == [key]

    again = client.post("/search", data={"chosen_fields": ["software-engineering"]})
    assert key in again.text, "a saved job must stay in the results"


def test_unsaving_takes_it_off_the_list(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/saved",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    assert fake.saved_rows

    removed = client.post("/saved/remove", data={"advert_key": key}, headers={"HX-Request": "true"})
    assert removed.status_code == 200
    assert fake.saved_rows == []
    assert "Save" in removed.text


def test_the_saved_page_lists_what_was_saved(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/saved",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    page = client.get("/saved")
    assert page.status_code == 200
    assert title in page.text
    assert "Acme" in page.text


def test_a_saved_job_that_was_applied_to_says_so(client: TestClient, fake):
    """Offering Apply on something already applied to is exactly the confusion the
    account exists to remove."""
    _signed_in(client)
    key, title = _first_result(client)
    client.post(
        "/saved",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    client.post(
        "/applications",
        data={"advert_key": key, "title": title, "company": "Acme", "url": "https://x"},
    )
    page = client.get("/saved")
    assert page.status_code == 200
    assert "applied" in page.text.lower()


def test_saving_needs_an_account(client: TestClient):
    response = client.post(
        "/saved", data={"advert_key": "k", "title": "t", "company": "c", "url": "u"}
    )
    assert response.status_code == 401


def test_a_saved_outage_does_not_take_the_search_down(client: TestClient, fake):
    """Same fail-open rule as applications: losing the saved list costs the Save
    button's state, never the job search."""
    _signed_in(client)
    fake.fail_with = supabase.SupabaseError("saved table unreachable")
    response = client.post("/search", data={"chosen_fields": ["software-engineering"]})
    assert response.status_code == 200
    assert "record__title" in response.text


# ------------------------------------------------- applying needs an account


def test_a_signed_out_visitor_is_sent_to_sign_in_before_applying(client: TestClient):
    """Everyone can see the jobs; applying is what needs the account, because
    recording it is what keeps the job out of tomorrow's results."""
    response = client.post("/search", data={"chosen_fields": ["software-engineering"]})
    assert response.status_code == 200
    assert "why=apply" in response.text, "Apply must route through sign-in"
    assert "why=save" in response.text, "Save must route through sign-in"


def test_sign_in_from_apply_is_a_short_step_not_a_lecture(client: TestClient):
    page = client.get("/login", params={"why": "apply"})
    assert page.status_code == 200
    assert "One step first." in page.text
    assert "stops being offered to you tomorrow" not in page.text
    assert 'class="notice"' not in page.text


def test_sign_in_returns_you_to_where_you_were(client: TestClient):
    response = client.post(
        "/login",
        data={"email": "jane@example.com", "password": "hunter2hunter2", "next": "/saved"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/saved"


def test_next_cannot_be_pointed_off_site(client: TestClient):
    """An open redirect on a sign-in page lends this domain's trust to someone else's."""
    for hostile in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)"):
        response = client.post(
            "/login",
            data={"email": "jane@example.com", "password": "hunter2hunter2", "next": hostile},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/", hostile


# ------------------------------------------------------------ google sign-in


def _google_account() -> supabase.Account:
    account = _account("aoife@gmail.com")
    account.name, account.provider, account.joined = "Aoife Byrne", "google", "2026-09-20"
    return account


def _start_google(client: TestClient, next_to: str = "/") -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse

    response = client.get("/auth/google", params={"next": next_to}, follow_redirects=False)
    assert response.status_code == 303
    url = urlparse(response.headers["location"])
    assert url.netloc == "proj.supabase.co" and url.path == "/auth/v1/authorize"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def test_google_sign_in_round_trip(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The PKCE code flow: the verifier stays with us, only its hash goes to Google, and
    the code that comes back is exchanged alongside the original verifier."""
    import base64
    import hashlib

    params = _start_google(client, next_to="/companies")
    assert params["provider"] == "google"
    assert params["code_challenge_method"] == "s256"
    assert params["redirect_to"].endswith("/auth/callback")

    seen: dict[str, str] = {}

    def exchange(code, verifier):
        seen.update(code=code, verifier=verifier)
        return _google_account()

    monkeypatch.setattr(supabase, "exchange_code", exchange)
    response = client.get("/auth/callback", params={"code": "abc"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/companies"
    assert seen["code"] == "abc"
    digest = hashlib.sha256(seen["verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == params["code_challenge"]

    profile = client.get("/profile").text
    assert "Hi, Aoife." in profile
    assert "Signed in with Google" in profile


def test_a_callback_this_browser_never_started_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(supabase, "exchange_code", lambda code, verifier: _google_account())
    response = client.get("/auth/callback", params={"code": "abc"}, follow_redirects=False)
    assert response.status_code == 400
    assert "expired" in response.text
    assert "sp_account" not in response.cookies


def test_cancelling_at_google_says_so_plainly(client: TestClient):
    _start_google(client)
    response = client.get("/auth/callback", params={"error": "access_denied"})
    assert response.status_code == 400
    assert "cancelled" in response.text


def test_a_code_returned_to_the_home_page_still_finishes_signing_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Supabase sends people to the Site URL when the callback is not on its allow list."""
    monkeypatch.setattr(supabase, "exchange_code", lambda code, verifier: _google_account())
    _start_google(client)
    response = client.get("/", params={"code": "abc"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/callback?code=abc"


def test_the_sign_in_page_leads_with_google(client: TestClient):
    page = client.get("/login").text
    assert "Continue with Google" in page
    assert page.index("Continue with Google") < page.index('name="password"')


def test_the_sign_in_page_is_email_only_when_google_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(supabase, "providers", lambda: {"google": False})
    page = client.get("/login").text
    assert "Continue with Google" not in page
    assert 'name="password"' in page


# ------------------------------------------------------------- the cookies


def test_the_account_has_a_cookie_of_its_own(client: TestClient):
    """Profile and account together outgrew what a browser keeps in one cookie."""
    _signed_in(client)
    assert client.cookies.get("sp_account")
    session = client.cookies.get("session") or ""
    assert "access-token" not in session


def test_a_cv_search_and_a_google_account_both_survive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Each cookie must stay under the ~4KB browsers silently drop, with a real-sized
    access token rather than the short stand-in the other tests use."""
    big = _google_account()
    big.access_token = "eyJ" + "x" * 1400
    big.avatar = "https://lh3.googleusercontent.com/a/" + "y" * 90
    monkeypatch.setattr(supabase, "exchange_code", lambda code, verifier: big)
    _start_google(client)
    client.get("/auth/callback", params={"code": "abc"})

    cv = b"Senior Software Engineer. Python, Kubernetes, Kafka, Terraform, AWS. " * 200
    client.post("/profile/cv", files={"resume": ("cv.txt", cv, "text/plain")})
    client.post(
        "/search",
        data={"chosen_fields": ["backend"], "years": "6", "use_cv": "1"},
    )
    for name in ("session", "sp_account"):
        value = client.cookies.get(name) or ""
        assert value and len(value) < 4000, f"{name} is {len(value)} bytes"
    assert "Hi, Aoife." in client.get("/profile").text


def test_an_account_from_before_the_split_is_moved_across(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    import base64
    import json as _json

    from itsdangerous import TimestampSigner

    from jobfinder.core.config import settings

    legacy = {"account": _account().to_session()}
    raw = base64.b64encode(_json.dumps(legacy).encode())
    client.cookies.set("session", TimestampSigner(settings.session_secret).sign(raw).decode())

    page = client.get("/")
    assert "jane@example.com" in page.text
    assert client.cookies.get("sp_account")


def test_signing_out_clears_the_account_cookie(client: TestClient):
    _signed_in(client)
    client.post("/logout")
    assert not client.cookies.get("sp_account")
    assert "Sign in" in client.get("/").text


# ----------------------------------------------------------------- profile


def test_saved_and_applied_live_on_the_profile(client: TestClient, fake):
    _signed_in(client)
    for path, tab in (("/saved", "saved"), ("/applications", "applied")):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/profile?tab={tab}"

    fake.saved_rows.append({"advert_key": "s" * 32, "title": "Data Engineer",
                            "company": "Stripe", "url": "https://x", "saved_at": ""})
    fake.rows.append({"advert_key": "a" * 32, "title": "Nurse", "company": "HSE",
                      "url": "https://y", "applied_at": ""})
    page = client.get("/profile?tab=applied").text
    assert "Data Engineer" in page and "Nurse" in page
    assert re.search(r'id="tab-applied"[^>]*aria-selected="true"', page)
    assert "Sign out" in page


def test_the_menu_bar_folds_saved_and_applied_under_the_account(client: TestClient):
    _signed_in(client)
    page = client.get("/").text
    assert 'href="/profile"' in page
    assert 'class="burger"' in page


def test_removing_from_the_profile_drops_just_that_row(client: TestClient, fake):
    _signed_in(client)
    fake.saved_rows.append({"advert_key": "s" * 32, "title": "t", "company": "c",
                            "url": "u", "saved_at": ""})
    response = client.post(
        "/saved/remove", data={"advert_key": "s" * 32},
        headers={"HX-Request": "true", "X-From": "profile"},
    )
    assert response.status_code == 200 and response.text == ""
    assert "HX-Refresh" not in response.headers
    assert fake.saved_rows == []


def test_undo_in_the_results_puts_apply_back_without_a_reload(client: TestClient, fake):
    _signed_in(client)
    key, title = _first_result(client)
    vals = {"advert_key": key, "title": title, "company": "Acme", "url": "https://x/job"}
    client.post("/applications", data=vals)
    response = client.post("/applications/remove", data=vals, headers={"HX-Request": "true"})
    assert "HX-Refresh" not in response.headers
    assert "btn--apply" in response.text and 'href="https://x/job"' in response.text


# ----------------------------------------------------- a fresh search per person


def _ticked(page: str) -> list[str]:
    return re.findall(r'name="chosen_fields" value="([^"]+)"\s+checked', page)


def _can_page(client: TestClient) -> bool:
    """Whether a search is still in the session: page two re-runs it without fields."""
    return client.post("/search?page=2", data={}).status_code == 200


def test_every_visit_starts_with_a_blank_form(client: TestClient):
    """The owner's rule: a refresh, the logo or a later visit all begin again."""
    client.post("/search", data={"chosen_fields": ["accounting"]})
    home = client.get("/")
    assert _ticked(home.text) == []
    assert 'class="record' not in home.text
    # Nor may Back bring the old page out of the browser's memory.
    assert home.headers["cache-control"] == "no-store"


def test_paging_still_works_while_on_the_page(client: TestClient):
    client.post("/search", data={"chosen_fields": ["backend"]})
    assert _can_page(client)


def test_signing_in_starts_a_fresh_search(client: TestClient):
    """Regression: the last visitor's fields, and the skills from their CV, carried
    over to whoever signed in next on the same browser."""
    client.post("/search", data={"chosen_fields": ["machine-learning", "data-science"]})
    _signed_in(client)
    assert not _can_page(client)
    assert _ticked(client.get("/").text) == []


def test_google_sign_in_also_starts_fresh(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(supabase, "exchange_code", lambda code, verifier: _google_account())
    client.post("/search", data={"chosen_fields": ["backend"]})
    _start_google(client)
    client.get("/auth/callback", params={"code": "abc"})
    assert not _can_page(client)


def test_signing_out_clears_the_search(client: TestClient):
    _signed_in(client)
    client.post("/search", data={"chosen_fields": ["backend"]})
    client.post("/logout")
    assert not _can_page(client)


def test_staying_signed_in_keeps_the_search(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """The hourly token refresh is not a new person, and must not lose their search."""
    _signed_in(client)
    client.post("/search", data={"chosen_fields": ["backend"]})
    stale = _account()
    stale.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
    monkeypatch.setattr(supabase.Account, "from_session", classmethod(lambda cls, d: stale if d else None))
    monkeypatch.setattr(supabase, "refresh", lambda account: _account())
    assert _can_page(client)


# ---------------------------------------------------------------- the profile CV
#
# The CV goes up once, on the profile, and every search after that is ranked against
# it. What is kept is the reading of it; the document itself is never stored.

CV_BYTES = b"""
Jane Doe
jane@example.com
+353 87 123 4567
Senior Software Engineer at Acme (2019-2026)
Python, Kubernetes, Kafka, Terraform, AWS. 6 years of experience.
"""


def _with_cv(client: TestClient) -> TestClient:
    _signed_in(client)
    response = client.post(
        "/profile/cv",
        files={"resume": ("jane-doe-cv.txt", CV_BYTES, "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    return client


def test_a_cv_is_read_into_the_profile_and_the_document_is_not_kept(
    client: TestClient, fake
):
    _with_cv(client)
    cv = fake.profile["cv"]
    assert cv["name"] == "jane-doe-cv.txt"
    assert "python" in cv["skills"]
    assert cv["seniority"] == "senior"
    assert cv["years"] == 6
    assert cv["bytes"] == len(CV_BYTES)
    kept = json.dumps(fake.profile).lower()
    # Nothing that identifies the person, and none of the document's own text.
    assert "jane@example.com" not in kept
    assert "123 4567" not in kept
    assert "senior software engineer at acme" not in kept
    assert "text" not in cv


def test_the_upload_answers_with_the_floating_sheet(client: TestClient, fake):
    _signed_in(client)
    response = client.post(
        "/profile/cv",
        files={"resume": ("jane-doe-cv.txt", CV_BYTES, "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert 'id="cvpanel"' in response.text
    assert "data-cvstage" in response.text
    assert "is-fresh" in response.text
    assert "Replace with a newer CV" in response.text and "Remove" in response.text
    # The header's "CV added" step is updated alongside, without a reload.
    assert 'id="step-cv"' in response.text and 'hx-swap-oob="true"' in response.text
    assert "<!doctype html>" not in response.text.lower()


def test_the_profile_shows_the_cv_or_asks_for_one(client: TestClient, fake):
    _signed_in(client)
    empty = client.get("/profile").text
    assert "Drop your CV here" in empty and "data-cvstage" not in empty
    assert "Add your CV" in empty

    _with_cv(client)
    page = client.get("/profile").text
    assert "data-cvstage" in page and "jane-doe-cv.txt" in page
    assert "CV added" in page
    assert "/static/cvsheet-1.js" in page


def test_a_new_upload_replaces_the_old_one(client: TestClient, fake):
    _with_cv(client)
    client.post(
        "/profile/cv",
        files={"resume": ("newer.txt", b"Data Analyst. SQL, Tableau, Excel.", "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert fake.profile["cv"]["name"] == "newer.txt"
    assert "kubernetes" not in fake.profile["cv"]["skills"]


def test_removing_the_cv_deletes_the_reading(client: TestClient, fake):
    _with_cv(client)
    fake.profile["years"] = 3
    response = client.post("/profile/cv/remove", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert fake.profile["cv"] is None
    assert fake.profile["years"] == 3, "removing the CV must leave the details alone"
    assert "Drop your CV here" in response.text


def test_an_oversized_cv_is_refused_and_nothing_is_kept(client: TestClient, fake):
    _signed_in(client)
    huge = b"x" * (5 * 1024 * 1024 + 10)
    response = client.post(
        "/profile/cv",
        files={"resume": ("big.txt", huge, "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert "larger than 5 MB" in response.text
    assert fake.profile is None


def test_a_file_with_no_text_says_so(client: TestClient, fake):
    _signed_in(client)
    response = client.post(
        "/profile/cv",
        files={"resume": ("scan.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")},
        headers={"HX-Request": "true"},
    )
    assert "could not find any text" in response.text
    assert fake.profile is None


def test_a_cv_that_cannot_be_saved_says_so(client: TestClient, fake):
    _signed_in(client)
    fake.profile_fails_with = supabase.SupabaseError("relation does not exist")
    response = client.post(
        "/profile/cv",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "could not be saved just now" in response.text


def test_without_javascript_an_upload_comes_back_to_the_profile(client: TestClient, fake):
    _signed_in(client)
    response = client.post(
        "/profile/cv",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/profile#cv"
    failed = client.post(
        "/profile/cv",
        files={"resume": ("big.txt", b"x" * (5 * 1024 * 1024 + 10), "text/plain")},
        follow_redirects=False,
    )
    assert failed.headers["location"].startswith("/profile?cv_error=too-big")
    assert "larger than 5 MB" in client.get(failed.headers["location"]).text


def test_the_cv_needs_an_account(client: TestClient):
    response = client.post(
        "/profile/cv",
        files={"resume": ("cv.txt", CV_BYTES, "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 401
    assert client.post("/profile/cv/remove", headers={"HX-Request": "true"}).status_code == 401


# ------------------------------------------------------- searching with the CV


def test_a_search_is_ranked_against_the_stored_cv(client: TestClient, fake):
    _with_cv(client)
    ranked = client.post(
        "/search", data={"chosen_fields": ["backend"], "use_cv": "1"},
        headers={"HX-Request": "true"},
    ).text
    assert "closest to your CV first" in ranked
    assert "jane-doe-cv.txt" in ranked

    plain = client.post(
        "/search", data={"chosen_fields": ["backend"]}, headers={"HX-Request": "true"}
    ).text
    assert "best matches first" in plain, "switching the CV off leaves it out"


def test_paging_keeps_ranking_with_the_cv(client: TestClient, fake):
    _with_cv(client)
    client.post("/search", data={"chosen_fields": ["backend"], "use_cv": "1"})
    page_two = client.post(
        "/search?page=2",
        data={"chosen_fields": ["backend"], "use_cv": "1"},
        headers={"HX-Request": "true"},
    )
    assert page_two.status_code == 200
    assert "closest to your CV first" in page_two.text


def test_the_finder_says_whose_cv_ranks_the_results(client: TestClient, fake):
    home = client.get("/").text
    assert "Rank jobs against your CV" in home and 'href="/login?next=/profile"' in home

    _signed_in(client)
    assert "Add your CV to your profile" in client.get("/").text

    _with_cv(client)
    home = client.get("/").text
    assert "Ranking with your CV" in home and "jane-doe-cv.txt" in home
    assert 'name="use_cv" value="1" checked' in home


def test_typed_experience_overrides_the_cv(client: TestClient, fake):
    """The CV says six years; the slider says one. The slider wins."""
    _with_cv(client)
    response = client.post(
        "/search", data={"chosen_fields": ["backend"], "years": "1", "use_cv": "1"}
    )
    assert "asking 1 year or less" in response.text


def test_a_blank_slider_shows_everything_even_when_the_cv_states_years(
    client: TestClient, fake
):
    """Not stated means not stated: the CV's six years must not quietly filter."""
    import re

    def total(page: str) -> int:
        match = re.search(r"([\d,]+) (?:job|internship)", page)
        return int(match.group(1).replace(",", "")) if match else 0

    _with_cv(client)
    with_cv = total(client.post(
        "/search", data={"chosen_fields": ["backend"], "years": "", "use_cv": "1"}
    ).text)
    without = total(client.post(
        "/search", data={"chosen_fields": ["backend"], "years": ""}
    ).text)
    assert with_cv == without


def test_fields_the_cv_points_to_are_offered_below_the_chosen_ones(
    client: TestClient, fake
):
    _signed_in(client)
    fake.profile = {"cv": {
        "name": "cv.pdf", "skills": ["excel"], "fields": ["accounting"], "terms": [],
        "summary": "An accountant.", "seniority": "mid", "years": 4,
    }}
    page = client.post(
        "/search", data={"chosen_fields": ["backend"], "use_cv": "1"},
        headers={"HX-Request": "true"},
    ).text
    assert "Your CV also points to" in page and "Accounting" in page
    assert "Where your CV points" in page


def test_a_profile_outage_does_not_take_the_search_down(client: TestClient, fake):
    _with_cv(client)
    fake.profile_fails_with = httpx.ConnectError("down")
    response = client.post("/search", data={"chosen_fields": ["backend"], "use_cv": "1"})
    assert response.status_code == 200
    assert "best matches first" in response.text


# ----------------------------------------------------------------- the details


def test_details_are_saved_and_offered_back_on_the_finder(client: TestClient, fake):
    _signed_in(client)
    response = client.post(
        "/profile/details",
        data={"chosen_fields": ["backend", "not-a-field", "backend"], "years": "3",
              "include_remote": "1"},
        headers={"HX-Request": "true"},
    )
    assert "Saved" in response.text
    assert fake.profile["fields"] == ["backend"]
    assert fake.profile["years"] == 3
    assert fake.profile["include_remote"] is True
    assert fake.profile["graduate_only"] is False

    home = client.get("/").text
    assert 'id="fill-me"' in home and "Use my profile" in home
    # Offered, never applied: the form itself still starts blank.
    assert not re.findall(r'value="backend"\s+checked', home)


def test_any_on_the_profile_slider_saves_no_years(client: TestClient, fake):
    _signed_in(client)
    client.post("/profile/details", data={"chosen_fields": ["backend"], "years": "-1"},
                headers={"HX-Request": "true"})
    assert fake.profile["years"] is None


def test_saving_details_leaves_the_cv_alone(client: TestClient, fake):
    _with_cv(client)
    client.post("/profile/details", data={"chosen_fields": ["backend"]},
                headers={"HX-Request": "true"})
    assert fake.profile["cv"]["name"] == "jane-doe-cv.txt"


def test_the_profile_marks_what_is_set_up(client: TestClient, fake):
    _signed_in(client)
    page = client.get("/profile").text
    assert "Say what you are after" in page
    fake.profile = {"fields": ["backend"], "years": None}
    page = client.get("/profile").text
    assert "Details saved" in page
    assert re.search(r'value="backend"\s+checked', page)


def test_a_failed_details_save_says_so(client: TestClient, fake):
    _signed_in(client)
    fake.profile_fails_with = supabase.SupabaseError("nope")
    response = client.post("/profile/details", data={"chosen_fields": ["backend"]},
                           headers={"HX-Request": "true"})
    assert "Could not save" in response.text


def test_the_profile_still_loads_when_the_profile_row_cannot(client: TestClient, fake):
    _signed_in(client)
    fake.profile_fails_with = httpx.ConnectError("down")
    page = client.get("/profile")
    assert page.status_code == 200
    assert "could not be loaded just now" in page.text
