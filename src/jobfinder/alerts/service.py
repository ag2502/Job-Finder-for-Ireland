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
    """Either shape of Supabase admin key. The legacy `service_role` key is a JWT and is
    also sent as the bearer token; the newer `sb_secret_...` key is not a token at all,
    and Supabase rejects it there, so it goes in `apikey` alone (as core/supabase.py
    does for publishable keys)."""
    key = settings.supabase_service_role_key.strip()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _get(path: str, params: dict) -> list[dict]:
    response = httpx.get(f"{settings.supabase_url}/rest/v1/{path}", headers=_headers(),
                         params=params, timeout=TIMEOUT)
    if response.status_code >= 400:
        raise ServiceError(f"{path}: HTTP {response.status_code} {response.text[:200]}")
    return response.json()


def subscribers() -> list[dict]:
    """Every account with at least one alert on."""
    kinds = "internships.eq.true,graduate.eq.true,part_time.eq.true,jobs.eq.true"
    try:
        return _get("alerts", {"select": "*", "or": f"({kinds})", "limit": "5000"})
    except ServiceError as exc:
        # `part_time` arrived after the table did, and needs schema.sql re-run in
        # Supabase. Until it is, every other alert still goes out.
        if "part_time" not in str(exc):
            raise
        kinds = kinds.replace("part_time.eq.true,", "")
        return _get("alerts", {"select": "*", "or": f"({kinds})", "limit": "5000"})


def profiles(user_ids: list[str]) -> dict[str, dict]:
    """The saved fields and years that the alerts search with, by account."""
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


def event_reminders(since: datetime) -> list[dict]:
    """Every reminder for an event starting after `since`. Empty, rather than an error,
    while `schema.sql` has not been re-run to create the table."""
    try:
        return _get("event_reminders", {"select": "*", "starts_at": f"gte.{since.isoformat()}",
                                        "order": "starts_at.asc", "limit": "10000"})
    except ServiceError as exc:
        if "event_reminders" in str(exc) or "404" in str(exc):
            return []
        raise


def finished_reminders(before: datetime) -> list[dict]:
    return _get("event_reminders", {"select": "id", "starts_at": f"lt.{before.isoformat()}",
                                    "limit": "10000"})


def update_reminder(reminder_id: str, **columns) -> None:
    response = httpx.patch(
        f"{settings.supabase_url}/rest/v1/event_reminders", headers=_headers(),
        params={"id": f"eq.{reminder_id}"}, json=columns, timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ServiceError(f"update_reminder: HTTP {response.status_code} {response.text[:200]}")


def delete_reminder(reminder_id: str) -> None:
    response = httpx.delete(
        f"{settings.supabase_url}/rest/v1/event_reminders", headers=_headers(),
        params={"id": f"eq.{reminder_id}"}, timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise ServiceError(f"delete_reminder: HTTP {response.status_code} {response.text[:200]}")
