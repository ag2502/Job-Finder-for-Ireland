"""Accounts, and the record of what a searcher has applied to.

Supabase is stubbed. These tests are about what the finder does with an account - that a
job applied to leaves the results, that the toggle brings it back, that an outage fails
open - none of which needs a real network call to prove, and all of which would become
untestable if it did.
"""

from __future__ import annotations

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
        self.fail_with: Exception | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
        monkeypatch.setattr(supabase, "configured", lambda: True)
        monkeypatch.setattr(supabase, "sign_in", lambda email, password: _account(email))
        monkeypatch.setattr(supabase, "sign_up", lambda email, password: _account(email))
        monkeypatch.setattr(supabase, "sign_out", lambda account: None)
        monkeypatch.setattr(supabase, "list_applications", self._list)
        monkeypatch.setattr(supabase, "add_application", self._add)
        monkeypatch.setattr(supabase, "remove_application", self._remove)
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
    assert page.headers["location"] == "/login"


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


def test_a_signed_out_visitor_is_sent_to_sign_in(client: TestClient):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_signing_in_opens_the_site(client: TestClient):
    _signed_in(client)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Every Dublin opening" in response.text


@pytest.mark.parametrize("path", ["/login", "/signup", "/privacy"])
def test_the_pages_needed_before_signing_up_stay_public(client: TestClient, path: str):
    """Privacy especially: someone must be able to read what an account will store
    about them before being asked to create one."""
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200


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
        assert "Every Dublin opening" in response.text
