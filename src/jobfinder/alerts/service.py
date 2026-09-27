"""Every subscriber's alert settings, read with the service role key.

This is the one place in the project that uses that key, and it only ever runs in
GitHub Actions (`jobfinder send-alerts` in crawl.yml). The website never has it: see
`core/config.py`. Everything here is a plain PostgREST call, like core/supabase.py.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from jobfinder.core.config import settings

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class ServiceError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def _headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _get(path: str, params: dict) -> list[dict]:
    response = httpx.get(f"{settings.supabase_url}/rest/v1/{path}", headers=_headers(),
                         params=params, timeout=TIMEOUT)
    if response.status_code >= 400:
        raise ServiceError(f"{path}: HTTP {response.status_code} {response.text[:200]}")
    return response.json()


def subscribers() -> list[dict]:
    """Every account with at least one alert on."""
    return _get("alerts", {
        "select": "*",
        "or": "(internships.eq.true,graduate.eq.true,jobs.eq.true)",
        "limit": "5000",
    })


def profiles(user_ids: list[str]) -> dict[str, dict]:
    """The saved fields and years that a jobs alert searches with, by account."""
    out: dict[str, dict] = {}
    for start in range(0, len(user_ids), 100):
        chunk = user_ids[start:start + 100]
        for row in _get("profiles", {
            "select": "user_id,fields,years,include_remote",
            "user_id": f"in.({','.join(chunk)})",
        }):
            out[row["user_id"]] = row
    return out


def mark_sent(user_id: str, when: datetime) -> None:
    response = httpx.patch(
        f"{settings.supabase_url}/rest/v1/alerts", headers=_headers(),
        params={"user_id": f"eq.{user_id}"}, json={"last_sent_at": when.isoformat()},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ServiceError(f"mark_sent: HTTP {response.status_code} {response.text[:200]}")
