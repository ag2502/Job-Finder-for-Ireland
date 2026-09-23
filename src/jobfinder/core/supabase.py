"""Supabase client: accounts, and the record of what a searcher has applied to.

Talks to Supabase over its **REST API**, not a Postgres connection. That is deliberate,
and it is the same reasoning that `api/index.py` sets out for the job data: a serverless
function has no stable place to keep a connection pool, and a pool per cold start is how
a free database tier gets exhausted. Over HTTPS there is nothing to keep warm, and a
request that fails, fails in a hundred milliseconds instead of hanging on a socket.

**The job data does not live here.** Searches read the local snapshot exactly as before.
Supabase holds two things only - who someone is, and which adverts they have applied to -
so what crosses the network is a few hundred bytes per action rather than a dataset per
page load.

Every call is scoped by the signed-in user's own token, so PostgREST applies the row
level security policy in `schema.sql` and the database itself enforces that one account
cannot read another's applications. The service role key is deliberately never used:
filtering by `user_id` in application code would work right up until the day a query is
written without the filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from jobfinder.core.config import settings

logger = logging.getLogger(__name__)

# Applications are small and a searcher only has so many; one page is always enough.
MAX_APPLICATIONS = 1000

# Refresh a little before the token actually expires, so a request that takes a moment
# to arrive does not land with a credential that went stale in flight.
EXPIRY_MARGIN = timedelta(seconds=60)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


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
    expires_in = payload.get("expires_in") or 3600
    return Account(
        user_id=user.get("id", ""),
        email=user.get("email", ""),
        access_token=payload.get("access_token", ""),
        refresh_token=payload.get("refresh_token", ""),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).timestamp(),
    )


# --------------------------------------------------------------------------- auth


def sign_up(email: str, password: str) -> Account | None:
    """Create an account.

    Returns None when the project is set to confirm addresses by email: there is no
    session yet, because the person has to follow a link first, and the caller needs to
    say so rather than pretending they are signed in.
    """
    base, _ = _require_config()
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    if not renewed.user_id:
        renewed.user_id = account.user_id
    if not renewed.email:
        renewed.email = account.email
    return renewed


def sign_out(account: Account) -> None:
    """Revoke the refresh token server-side.

    Clearing the cookie is what actually signs the person out of this site; this stops
    the refresh token being usable if it were ever captured. A failure here is logged
    rather than raised - a sign-out that reports an error while having cleared the
    session is worse than useless to the person doing it.
    """
    base, _ = _require_config()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            client.post(
                f"{base}/auth/v1/logout", headers=_auth_headers(account.access_token)
            )
    except httpx.HTTPError:
        logger.info("supabase sign-out call failed; session cleared regardless")


# -------------------------------------------------------------- applications


def list_applications(account: Account) -> list[dict]:
    """Every advert this account has marked as applied to, newest first."""
    base, _ = _require_config()
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
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
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.delete(
            f"{base}/rest/v1/saved_jobs",
            headers=_auth_headers(account.access_token),
            params={"advert_key": f"eq.{advert_key}"},
        )
    if response.status_code >= 400:
        raise SupabaseError(_message_from(response))
