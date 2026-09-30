"""Build and send each subscriber's digest of newly opened jobs.

Runs after every crawl (crawl.yml), and sends to a subscriber only when three things
hold: an alert is on, it is due (a day, or a week, since the last one), and something
new has opened that it covers. An email with nothing in it is never sent.

What counts as new is deliberately strict. A job must have been first seen after the
subscriber's last email, and, where the employer gives a posting date, posted within a
week of it. The second rule is what stops a newly crawled employer, whose adverts are
all "first seen" today, from arriving as a flood of months-old roles.

The matching is the site's own search (`web.app._search_results`), so an alert never
lists a job that the same search on the site would not.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from jinja2 import Environment, PackageLoader, select_autoescape

from jobfinder.alerts import service
from jobfinder.core.config import settings

logger = logging.getLogger(__name__)

DUE_AFTER = {"daily": timedelta(hours=20), "weekly": timedelta(days=6, hours=20)}
# An advert whose own date is this much older than the last email is not new, however
# recently we first read it.
POSTED_GRACE = timedelta(days=7)
# Jobs listed in one section of an email; the rest are one click away.
SHOWN = 12
MAX_EMAILS = 400

_env = Environment(
    loader=PackageLoader("jobfinder.alerts", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True, lstrip_blocks=True,
)


@dataclass
class Section:
    key: str
    heading: str
    noun: str            # "internship", "graduate programme", "job"
    items: list[dict]
    more_url: str

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class Summary:
    subscribers: int = 0
    due: int = 0
    sent: int = 0
    empty: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        text = (f"alerts: {self.subscribers} subscribers, {self.due} due, {self.sent} sent, "
                f"{self.empty} with nothing new, {self.failed} failed")
        return text + ("; " + "; ".join(self.notes) if self.notes else "")


def _time(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def is_due(row: dict, now: datetime) -> bool:
    last = _time(row.get("last_sent_at"))
    return last is None or now - last >= DUE_AFTER.get(row.get("frequency"), DUE_AFTER["daily"])


def since_for(row: dict) -> datetime:
    """New means new since the last email, or since the alert was switched on."""
    return _time(row.get("last_sent_at")) or _time(row.get("created_at")) or (
        datetime.now(timezone.utc) - timedelta(days=1)
    )


def _is_new(item: dict, since: datetime) -> bool:
    from jobfinder.web.app import _as_utc

    job = item["job"]
    first_seen, posted = _as_utc(job.first_seen_at), _as_utc(job.posted_at)
    if not first_seen or first_seen <= since:
        return False
    return posted is None or posted >= since - POSTED_GRACE


def _section(key: str, heading: str, noun: str, profile: dict, since: datetime) -> Section:
    from jobfinder.web.app import _search_results, _search_url

    context = _search_results(profile, per_page=100_000, since=since)
    items = [i for i in context["items"] if _is_new(i, since)]
    link = settings.site_url.rstrip("/") + _search_url(
        {**profile, "sort": "newest"}, {"view": "list"}
    )
    return Section(key, heading, noun, items, link)


def sections_for(row: dict, profile: dict | None, since: datetime) -> list[Section]:
    """What this subscriber asked for, each with the jobs new since `since`.

    A job appearing in two sections (a graduate programme in the searcher's own field)
    is listed once, in the first.
    """
    profile = profile or {}
    fields = [f for f in (profile.get("fields") or [])]
    out: list[Section] = []
    if row.get("internships"):
        out.append(_section("internships", "New internships", "internship",
                            {"fields": fields, "internships_only": True}, since))
    if row.get("graduate"):
        out.append(_section("graduate", "New graduate programmes", "graduate programme",
                            {"fields": fields, "graduate_only": True}, since))
    if row.get("jobs") and fields:
        out.append(_section("jobs", "New jobs in your fields", "job", {
            "fields": fields, "years": profile.get("years"),
            "include_remote": bool(profile.get("include_remote")),
        }, since))
    seen: set[int] = set()
    for section in out:
        section.items = [i for i in section.items if i["job"].id not in seen]
        seen.update(i["job"].id for i in section.items)
    return [s for s in out if s.items]


def _plural(n: int, noun: str) -> str:
    if n == 1:
        return f"1 new {noun}"
    return f"{n} new " + ("graduate programmes" if noun == "graduate programme" else noun + "s")


def subject(sections: list[Section]) -> str:
    parts = [_plural(s.count, s.noun) for s in sections]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return joined[0].upper() + joined[1:] + " in Ireland"


def compose(row: dict, sections: list[Section]) -> EmailMessage:
    site = settings.site_url.rstrip("/")
    unsubscribe = f"{site}/alerts/unsubscribe?token={row['token']}"
    context = {
        "sections": sections, "shown": SHOWN, "site": site,
        "manage": f"{site}/profile#alerts", "unsubscribe": unsubscribe,
        "frequency": row.get("frequency") or "daily",
    }
    message = EmailMessage()
    message["Subject"] = subject(sections)
    name, address = parseaddr(settings.smtp_from or settings.smtp_user)
    message["From"] = formataddr((name or "Sorted Place", address))
    message["To"] = row["email"]
    # One-click unsubscribe (RFC 8058): mail apps show their own button, which posts here.
    message["List-Unsubscribe"] = f"<{unsubscribe}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(_env.get_template("digest.txt").render(**context))
    message.add_alternative(_env.get_template("digest.html").render(**context), subtype="html")
    return message


def _smtp() -> smtplib.SMTP:
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(),
                                  timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.starttls(context=ssl.create_default_context())
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    return server


def run(*, dry_run: bool = False, now: datetime | None = None) -> Summary:
    """Send every digest that is due. Never raises for one subscriber's failure."""
    summary = Summary()
    if not service.configured():
        summary.notes.append("no service role key: nothing read (set SUPABASE_SERVICE_ROLE_KEY)")
        return summary
    sending = bool(settings.smtp_host) and not dry_run
    if not settings.smtp_host and not dry_run:
        summary.notes.append("no SMTP host set: reporting only, nothing sent")
    now = now or datetime.now(timezone.utc)
    rows = service.subscribers()
    summary.subscribers = len(rows)
    due = [r for r in rows if is_due(r, now)][:MAX_EMAILS]
    summary.due = len(due)
    profiles = service.profiles([r["user_id"] for r in due if r.get("jobs")]) if due else {}

    server = None
    try:
        for row in due:
            try:
                sections = sections_for(row, profiles.get(row["user_id"]), since_for(row))
            except Exception:  # noqa: BLE001 - one subscriber must not stop the rest
                logger.exception("could not build an alert")
                summary.failed += 1
                continue
            if not sections:
                summary.empty += 1
                continue
            message = compose(row, sections)
            if not sending:
                summary.sent += 1
                logger.info("would send %r", message["Subject"])
                continue
            try:
                server = server or _smtp()
                server.send_message(message)
                service.mark_sent(row["user_id"], now)
                summary.sent += 1
            except (smtplib.SMTPException, OSError, service.ServiceError):
                logger.exception("could not send an alert")
                summary.failed += 1
                server = None
    finally:
        if server is not None:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
    if not sending and summary.sent:
        summary.notes.append(f"{summary.sent} would have been sent")
    return summary
