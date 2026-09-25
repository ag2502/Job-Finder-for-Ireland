"""Supabase client: accounts, and the record of what a searcher has applied to.

Talks to Supabase over its **REST API**, not a Postgres connection. That is deliberate,
and it is the same reasoning that `api/index.py` sets out for the job data: a serverless
function has no stable place to keep a connection pool, and a pool per cold start is how
a free database tier gets exhausted. Over HTTPS there is nothing to keep warm, and a
request that fails, fails in a hundred milliseconds instead of hanging on a socket.

**The job data does not live here.** Searches read the local snapshot exactly as before.
Supabase holds only what belongs to a person - who they are, the adverts they have saved
or applied to, and their profile with the reading of their CV - so what crosses the
network is a few hundred bytes per action rather than a dataset per page load.

Every call is scoped by the signed-in user's own token, so PostgREST applies the row
level security policy in `schema.sql` and the database itself enforces that one account
cannot read another's applications. The service role key is deliberately never used:
filtering by `user_id` in application code would work right up until the day a query is
written without the filter.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import urlencode

import httpx

from jobfinder.core.config import settings

logger = logging.getLogger(__name__)

# Applications are small and a searcher only has so many; one page is always enough.
MAX_APPLICATIONS = 1000

# Refresh a little before the token actually expires, so a request that takes a moment
# to arrive does not land with a credential that went stale in flight.
EXPIRY_MARGIN = timedelta(seconds=60)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# One pool of kept-alive connections for every call. A fresh client per call paid a new
# TCP and TLS handshake to Supabase each time - two of them on every signed-in search,
# one for applications and one for saved jobs - which from a US function to an EU
# project is most of a second before a single row comes back.
_POOL = httpx.HTTPTransport(retries=1)


@contextmanager
def _client():
    """A client on the shared pool. Not closed on exit: closing it would close the pool."""
    yield httpx.Client(timeout=TIMEOUT, transport=_POOL)


class SupabaseError(RuntimeError):
    """A call to Supabase failed in a way the caller should show to the searcher."""


class NotConfigured(SupabaseError):
    """No Supabase project is set up for this deployment."""


@dataclass
class Account:
    """A signed-in searcher, as it is held in the session cookie.

    The tokens live here rather than in a server-side store because there is no server
    side to speak of: the function is stateless between requests. The cookie is signed,
    so the browser cannot alter any of it, and the access token is what authorises every
    later call on that person's behalf.
    """

    user_id: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: float
    # What the profile page shows. Defaulted so a cookie written before they existed
    # still reads; Google fills the first two, an email account has neither.
    name: str = ""
    avatar: str = ""
    provider: str = "email"
    joined: str = ""

    @property
    def expired(self) -> bool:
        deadline = datetime.fromtimestamp(self.expires_at, tz=timezone.utc)
        return datetime.now(timezone.utc) + EXPIRY_MARGIN >= deadline

    def to_session(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "name": self.name,
            "avatar": self.avatar,
            "provider": self.provider,
            "joined": self.joined,
        }

    @classmethod
    def from_session(cls, data: dict | None) -> Account | None:
        if not data:
            return None
        try:
            return cls(**data)
        except TypeError:  # a cookie written by an older build
            return None


def configured() -> bool:
    """Whether this deployment has a Supabase project to talk to.

    Accounts are an addition, not a requirement. With nothing configured the finder
    still searches, ranks and shows every job - it simply offers no sign-in, which is
    what a local checkout without secrets should do rather than erroring on every page.
    """
    return bool(settings.supabase_url and settings.supabase_anon_key)


def _require_config() -> tuple[str, str]:
    if not configured():
        raise NotConfigured("No Supabase project is configured for this deployment.")
    return settings.supabase_url.rstrip("/"), settings.supabase_anon_key


def _auth_headers(token: str | None = None) -> dict[str, str]:
    """Headers for a call made as `token`, or as nobody in particular.

    `apikey` names the project and is always sent. `Authorization` names the person, and
    is what row level security reads to decide which rows exist.

    Supabase issues two shapes of project key and they cannot be used the same way. The
    legacy `anon` key is itself a JWT, so sending it as the bearer token is what tells
    PostgREST to act as the `anon` role. The newer `sb_publishable_...` keys are opaque
    handles, not tokens - presenting one as a bearer credential is rejected, because it
    is not a credential. So it goes in `apikey` alone, which is all it was ever meant
    for, and the Authorization header is left off until there is a real user token.
    """
    _, project_key = _require_config()
    headers = {"apikey": project_key, "Content-Type": "application/json"}

    bearer = token or (project_key if _is_jwt(project_key) else None)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _is_jwt(value: str) -> bool:
    """Whether a key is a JWT, and so usable as a bearer token in its own right."""
    return value.startswith("eyJ")


def _message_from(response: httpx.Response) -> str:
    """Pull the human-readable part out of a Supabase error body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    for key in ("msg", "message", "error_description", "error", "hint"):
        value = body.get(key) if isinstance(body, dict) else None
        if isinstance(value, str) and value:
            return value
    return f"HTTP {response.status_code}"


def _account_from_token_response(payload: dict) -> Account:
    user = payload.get("user") or {}
    meta = user.get("user_metadata") or {}
    expires_in = payload.get("expires_in") or 3600
    avatar = meta.get("avatar_url") or meta.get("picture") or ""
    return Account(
        user_id=user.get("id", ""),
        email=user.get("email", ""),
        access_token=payload.get("access_token", ""),
        refresh_token=payload.get("refresh_token", ""),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).timestamp(),
        name=(meta.get("full_name") or meta.get("name") or "")[:80],
        # Only an https picture is kept: it is put straight into an <img>, and the
        # cookie has no room for anything long.
        avatar=avatar[:300] if avatar.startswith("https://") else "",
        provider=(user.get("app_metadata") or {}).get("provider") or "email",
        joined=(user.get("created_at") or "")[:10],
    )


# --------------------------------------------------------------------------- auth


def sign_up(email: str, password: str) -> Account | None:
    """Create an account.

    Returns None when the project is set to confirm addresses by email: there is no
    session yet, because the person has to follow a link first, and the caller needs to
    say so rather than pretending they are signed in.
    """
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/auth/v1/signup",
            headers=_auth_headers(),
            json={"email": email, "password": password},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))

    payload = response.json()
    if not payload.get("access_token"):
        return None
    return _account_from_token_response(payload)


def sign_in(email: str, password: str) -> Account:
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/auth/v1/token",
            params={"grant_type": "password"},
            headers=_auth_headers(),
            json={"email": email, "password": password},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return _account_from_token_response(response.json())


def refresh(account: Account) -> Account:
    """Exchange a refresh token for a new access token."""
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers=_auth_headers(),
            json={"refresh_token": account.refresh_token},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))

    renewed = _account_from_token_response(response.json())
    # A refresh response does not always carry the user object; keep what we know.
    for name in ("user_id", "email", "name", "avatar", "joined"):
        if not getattr(renewed, name):
            setattr(renewed, name, getattr(account, name))
    if renewed.provider == "email" and account.provider != "email":
        renewed.provider = account.provider
    return renewed


# ------------------------------------------------------------- google sign-in
#
# The PKCE code flow, which is what lets a server do OAuth without the tokens ever
# appearing in a URL. Supabase's default "implicit" flow returns them in the fragment
# after `#`, which browsers never send to a server - it only works for JavaScript apps.
#
# Here the site keeps a random verifier in its own session and sends Supabase a hash of
# it. Google sends the person back with a one-time code, and Supabase only exchanges
# that code for a session when it is presented alongside the original verifier - so a
# code that leaks, through a referrer or browser history, is useless on its own.

_PROVIDERS_TTL = 600.0
_providers_cache: tuple[float, dict[str, bool]] | None = None


def providers() -> dict[str, bool]:
    """Which sign-in methods the project has switched on, e.g. {"google": True}.

    Asked of Supabase rather than configured here, so turning Google on in the dashboard
    is the whole job; the button appears on its own. Remembered for ten minutes so the
    sign-in page does not pay a round trip every time. If Supabase cannot be asked, the
    answer is "yes": a button that fails is better than silently hiding the main way in.
    """
    global _providers_cache
    now = monotonic()
    if _providers_cache and now - _providers_cache[0] < _PROVIDERS_TTL:
        return _providers_cache[1]
    base, _ = _require_config()
    try:
        with _client() as client:
            response = client.get(f"{base}/auth/v1/settings", headers=_auth_headers())
        response.raise_for_status()
        external = response.json().get("external") or {}
        found = {k: bool(v) for k, v in external.items() if isinstance(v, bool)}
    except (httpx.HTTPError, ValueError, AttributeError):
        logger.info("could not read supabase auth settings; assuming google is on")
        found = {"google": True}
        now -= _PROVIDERS_TTL - 60  # try again in a minute rather than ten
    _providers_cache = (now, found)
    return found


def pkce_pair() -> tuple[str, str]:
    """A fresh (verifier, challenge) pair, the challenge being the S256 hash."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def google_authorize_url(redirect_to: str, challenge: str) -> str:
    """Where to send someone to sign in with Google."""
    base, _ = _require_config()
    query = urlencode(
        {
            "provider": "google",
            "redirect_to": redirect_to,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
        }
    )
    return f"{base}/auth/v1/authorize?{query}"


def exchange_code(code: str, verifier: str) -> Account:
    """Trade the code Google sent back, plus our verifier, for a signed-in session."""
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/auth/v1/token",
            params={"grant_type": "pkce"},
            headers=_auth_headers(),
            json={"auth_code": code, "code_verifier": verifier},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return _account_from_token_response(response.json())


def sign_out(account: Account) -> None:
    """Revoke the refresh token server-side.

    Clearing the cookie is what actually signs the person out of this site; this stops
    the refresh token being usable if it were ever captured. A failure here is logged
    rather than raised - a sign-out that reports an error while having cleared the
    session is worse than useless to the person doing it.
    """
    base, _ = _require_config()
    try:
        with _client() as client:
            client.post(
                f"{base}/auth/v1/logout", headers=_auth_headers(account.access_token)
            )
    except httpx.HTTPError:
        logger.info("supabase sign-out call failed; session cleared regardless")


# -------------------------------------------------------------- applications


def list_applications(account: Account) -> list[dict]:
    """Every advert this account has marked as applied to, newest first."""
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/applications",
            headers=_auth_headers(account.access_token),
            params={
                "select": "advert_key,title,company,url,applied_at",
                "order": "applied_at.desc",
                "limit": str(MAX_APPLICATIONS),
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return response.json()


def add_application(
    account: Account,
    *,
    advert_key: str,
    title: str,
    company: str,
    url: str,
) -> None:
    """Record that this account applied to an advert.

    The title, company and URL are stored alongside the key rather than looked up later.
    The snapshot only carries open Dublin roles, so an advert is gone from it within days
    of the employer closing it - and a history that blanks out as jobs close would be
    worth very little to the person reading it.

    Clicking Apply twice is an ordinary thing to do, so the second write updates rather
    than failing. That takes both halves: `resolution=merge-duplicates` asks for an
    upsert, and `on_conflict` names which constraint counts as a duplicate. Without the
    second, PostgREST infers the conflict target from the primary key - `id`, a fresh
    uuid on every insert, which therefore never conflicts - and the unique constraint on
    (user_id, advert_key) raises instead of merging.
    """
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/rest/v1/applications",
            params={"on_conflict": "user_id,advert_key"},
            headers={
                **_auth_headers(account.access_token),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json={
                "user_id": account.user_id,
                "advert_key": advert_key,
                "title": title[:300],
                "company": company[:200],
                "url": url[:1000],
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


def remove_application(account: Account, advert_key: str) -> None:
    """Undo a mark, so a mis-click does not hide a job for good."""
    base, _ = _require_config()
    with _client() as client:
        response = client.delete(
            f"{base}/rest/v1/applications",
            headers=_auth_headers(account.access_token),
            params={"advert_key": f"eq.{advert_key}"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


# ---------------------------------------------------------------- saved jobs
#
# Deliberately the mirror of the three functions above rather than a shared helper
# parameterised by table name. The two lists answer opposite questions - "what have I
# already acted on" versus "what do I still want to act on" - and they are read on
# different pages with different orderings. Collapsing them would save thirty lines and
# cost the next reader the ability to change one without thinking about the other.


def list_saved(account: Account) -> list[dict]:
    """Every advert this account has saved for later, newest first."""
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/saved_jobs",
            headers=_auth_headers(account.access_token),
            params={
                "select": "advert_key,title,company,url,saved_at",
                "order": "saved_at.desc",
                "limit": str(MAX_APPLICATIONS),
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return response.json()


def add_saved(
    account: Account,
    *,
    advert_key: str,
    title: str,
    company: str,
    url: str,
) -> None:
    """Save an advert to come back to.

    Upserts for the same reason applications do: hitting Save on something already
    saved is an ordinary thing to do, and `on_conflict` has to name the
    (user_id, advert_key) constraint or PostgREST infers the primary key and never
    finds a conflict.
    """
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/rest/v1/saved_jobs",
            params={"on_conflict": "user_id,advert_key"},
            headers={
                **_auth_headers(account.access_token),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json={
                "user_id": account.user_id,
                "advert_key": advert_key,
                "title": title[:300],
                "company": company[:200],
                "url": url[:1000],
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


def remove_saved(account: Account, advert_key: str) -> None:
    """Take an advert off the saved list."""
    base, _ = _require_config()
    with _client() as client:
        response = client.delete(
            f"{base}/rest/v1/saved_jobs",
            headers=_auth_headers(account.access_token),
            params={"advert_key": f"eq.{advert_key}"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


# ------------------------------------------------------------------ profiles
#
# One row per account, holding what the searcher told us and what was read from their
# CV. Written with an upsert that names only the columns being changed: PostgREST turns
# `resolution=merge-duplicates` into `on conflict do update set` for exactly the keys in
# the payload, so saving the details never clears the CV and replacing the CV never
# clears the details.


def get_profile(account: Account) -> dict | None:
    """This account's profile row, or None when it has never saved one."""
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/profiles",
            headers=_auth_headers(account.access_token),
            params={
                "select": "fields,years,include_remote,internships_only,graduate_only,cv,updated_at",
                "user_id": f"eq.{account.user_id}",
                "limit": "1",
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    rows = response.json()
    return rows[0] if rows else None


def save_profile(account: Account, **columns) -> None:
    """Write the given profile columns, leaving every other column as it was."""
    base, _ = _require_config()
    payload = {
        "user_id": account.user_id,
        **columns,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _client() as client:
        response = client.post(
            f"{base}/rest/v1/profiles",
            params={"on_conflict": "user_id"},
            headers={
                **_auth_headers(account.access_token),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=payload,
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


# ------------------------------------------------------------------ CV files
#
# The CV document and the versions tailored from it, in the private `cvs` bucket.
# Paths always begin with the account's own user id, which is what the storage policies
# in `schema.sql` check; a path built any other way is refused by the database.

CV_BUCKET = "cvs"


def cv_path(account: Account, folder: str, name: str) -> str:
    """Where a file of this account's goes: `<user id>/<folder>/<name>`."""
    return f"{account.user_id}/{folder}/{name}"


def upload_file(account: Account, path: str, data: bytes, content_type: str) -> None:
    if not path.startswith(f"{account.user_id}/"):
        raise SupabaseError("A file can only be stored in its owner's own folder.")
    base, _ = _require_config()
    headers = {**_auth_headers(account.access_token), "Content-Type": content_type,
               "x-upsert": "true"}
    with _client() as client:
        response = client.post(
            f"{base}/storage/v1/object/{CV_BUCKET}/{path}", headers=headers, content=data
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


def download_file(account: Account, path: str) -> bytes:
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/storage/v1/object/authenticated/{CV_BUCKET}/{path}",
            headers=_auth_headers(account.access_token),
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return response.content


def delete_files(account: Account, paths: list[str]) -> None:
    """Remove files outright. Missing ones are not an error: the goal is that they are gone."""
    paths = [p for p in paths if p]
    if not paths:
        return
    base, _ = _require_config()
    with _client() as client:
        response = client.request(
            "DELETE",
            f"{base}/storage/v1/object/{CV_BUCKET}",
            headers=_auth_headers(account.access_token),
            json={"prefixes": paths},
        )
    if response.status_code >= 400 and response.status_code != 404:
        raise SupabaseError(_message_from(response))


# -------------------------------------------------------------- tailored CVs

TAILORED_LIST_COLUMNS = (
    "id,status,advert_key,job_title,company,job_url,source_name,ats_before,ats_after,"
    "rounds,file_name,created_at,updated_at"
)


def create_tailored(account: Account, **columns) -> dict:
    base, _ = _require_config()
    with _client() as client:
        response = client.post(
            f"{base}/rest/v1/tailored_cvs",
            headers={**_auth_headers(account.access_token), "Prefer": "return=representation"},
            json={"user_id": account.user_id, **columns},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return response.json()[0]


def get_tailored(account: Account, tailored_id: str) -> dict | None:
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/tailored_cvs",
            headers=_auth_headers(account.access_token),
            params={"select": "*", "id": f"eq.{tailored_id}", "limit": "1"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    rows = response.json()
    return rows[0] if rows else None


def update_tailored(account: Account, tailored_id: str, **columns) -> None:
    base, _ = _require_config()
    with _client() as client:
        response = client.patch(
            f"{base}/rest/v1/tailored_cvs",
            headers={**_auth_headers(account.access_token), "Prefer": "return=minimal"},
            params={"id": f"eq.{tailored_id}"},
            json={**columns, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


def delete_tailored(account: Account, tailored_id: str) -> None:
    base, _ = _require_config()
    with _client() as client:
        response = client.delete(
            f"{base}/rest/v1/tailored_cvs",
            headers=_auth_headers(account.access_token),
            params={"id": f"eq.{tailored_id}"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))


def list_tailored(account: Account, *, status: str = "saved") -> list[dict]:
    """This account's tailored CVs of one status, newest first."""
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/tailored_cvs",
            headers=_auth_headers(account.access_token),
            params={
                "select": TAILORED_LIST_COLUMNS,
                "status": f"eq.{status}",
                "order": "created_at.desc",
                "limit": str(MAX_APPLICATIONS),
            },
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    return response.json()


def count_tailored_since(account: Account, since: datetime) -> int:
    """How many tailorings this account has started since `since`, drafts included."""
    base, _ = _require_config()
    with _client() as client:
        response = client.get(
            f"{base}/rest/v1/tailored_cvs",
            headers={**_auth_headers(account.access_token), "Prefer": "count=exact"},
            params={"select": "id", "created_at": f"gte.{since.isoformat()}", "limit": "1"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
    total = response.headers.get("content-range", "*/0").rsplit("/", 1)[-1]
    return int(total) if total.isdigit() else len(response.json())


def purge_stale_drafts(account: Account, older_than: datetime) -> None:
    """Drafts nobody came back to. Failing here must never fail the request."""
    base, _ = _require_config()
    try:
        with _client() as client:
            client.delete(
                f"{base}/rest/v1/tailored_cvs",
                headers=_auth_headers(account.access_token),
                params={"status": "eq.draft", "updated_at": f"lt.{older_than.isoformat()}"},
            )
    except httpx.HTTPError:
        logger.info("could not clear old tailoring drafts")


def table_status(tables: tuple[str, ...]) -> dict[str, str]:
    """Whether each table exists, asked as nobody: row level security returns no rows,
    so this reads no one's data, but a missing table answers 404. For setup checks."""
    base, _ = _require_config()
    out = {}
    with _client() as client:
        for table in tables:
            try:
                response = client.get(f"{base}/rest/v1/{table}", headers=_auth_headers(),
                                      params={"select": "*", "limit": "0"})
                out[table] = "ok" if response.status_code < 400 else (
                    "missing: run schema.sql" if response.status_code == 404
                    else f"HTTP {response.status_code}: {_message_from(response)[:120]}")
            except httpx.HTTPError as exc:
                out[table] = f"unreachable: {type(exc).__name__}"
    return out
