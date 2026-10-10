"""Email the people who pressed Remind me on a careers event.

Runs after every crawl with the job alerts (`jobfinder send-alerts`, crawl.yml), reading
every reminder with the service role key. For each one, until the event starts:

* **daily or weekly**, as the person chose, starting with the first run after they
  pressed it, which doubles as the confirmation;
* **the day before**, always, once, whatever the frequency;
* **nothing after**: once the event has happened the reminder is deleted.

A person following several events gets one email listing them all. Nothing is sent at
night (outside 07:00 to 22:00 Irish time); the crawl runs every two hours and the day
before window is wide enough that the next morning's run still catches it.

The times come from the events table, which the same crawl has just refreshed, so a
fair that moved its start time is reminded at the new one.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy.orm import Session

from jobfinder.alerts import service
from jobfinder.alerts.digest import DUE_AFTER, _smtp, _time
from jobfinder.core.config import settings
from jobfinder.events import listing

logger = logging.getLogger(__name__)

DAY_BEFORE = timedelta(hours=30)
SEND_FROM_HOUR, SEND_UNTIL_HOUR = 7, 22
MAX_EMAILS = 400

_env = Environment(
    loader=PackageLoader("jobfinder.alerts", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True, lstrip_blocks=True,
)


@dataclass
class Item:
    row: dict
    title: str
    url: str
    starts: datetime        # Irish time
    when: str               # "Thu 15 Oct 2026, 10:00 to 13:00"
    where: str
    countdown: str          # "Tomorrow", "In 5 days"
    page: str               # the event's page on Sorted Place, or the listing itself
    calendar: str | None
    day_before: bool


@dataclass
class Summary:
    reminders: int = 0
    sent: int = 0
    finished: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        text = (f"event reminders: {self.reminders} set, {self.sent} emails sent, "
                f"{self.finished} finished and removed, {self.failed} failed")
        return text + ("; " + "; ".join(self.notes) if self.notes else "")


def due(row: dict, starts: datetime, now: datetime) -> str | None:
    """Why this reminder goes out now ("day_before" or "regular"), or None."""
    until = starts - now
    if until <= timedelta(0):
        return None
    if until <= DAY_BEFORE:
        return None if row.get("day_before_sent") else "day_before"
    last = _time(row.get("last_sent_at"))
    every = DUE_AFTER.get(row.get("frequency") or "weekly", DUE_AFTER["weekly"])
    return "regular" if last is None or now - last >= every else None


def item_for(session: Session, row: dict, now: datetime) -> tuple[Item | None, bool]:
    """The email line for one reminder, and whether its event is over."""
    site = settings.site_url.rstrip("/")
    view = listing.by_key(session, row["event_key"], now)
    if view is not None:
        if view.status == "done" or view.event.cancelled:
            return None, True
        starts_utc = view.starts.astimezone(timezone.utc)
        reason = due(row, starts_utc, now)
        if reason is None:
            return None, False
        return Item(
            row=row, title=view.event.title, url=view.event.url, starts=view.starts,
            when=f"{view.date_label}, {view.time_label}", where=view.where,
            countdown=view.countdown(now), page=f"{site}/events/{view.id}",
            calendar=f"{site}/events/{view.id}/calendar.ics", day_before=reason == "day_before",
        ), False
    # The event has left the snapshot: remind from what the row kept.
    starts_utc = _time(row.get("starts_at"))
    if starts_utc is None or starts_utc + timedelta(hours=12) <= now:
        return None, True
    reason = due(row, starts_utc, now)
    if reason is None:
        return None, False
    starts = starts_utc.astimezone(listing.DUBLIN)
    days = (starts.date() - now.astimezone(listing.DUBLIN).date()).days
    return Item(
        row=row, title=row["title"], url=row["url"], starts=starts,
        when=f"{starts:%a %-d %b %Y, %H:%M}", where="", countdown=(
            "Today" if days <= 0 else "Tomorrow" if days == 1 else f"In {days} days"),
        page=row["url"], calendar=None, day_before=reason == "day_before",
    ), False


def subject(items: list[Item]) -> str:
    first = items[0]
    if len(items) == 1:
        return f"{first.countdown}: {first.title}"
    return f"{len(items)} events you asked about, the first {first.countdown.lower()}"


def compose(email: str, items: list[Item]) -> EmailMessage:
    site = settings.site_url.rstrip("/")
    context = {"items": items, "site": site,
               "stop": lambda item: f"{site}/reminders/stop?token={item.row['token']}"}
    message = EmailMessage()
    message["Subject"] = subject(items)
    name, address = parseaddr(settings.smtp_from or settings.smtp_user)
    message["From"] = formataddr((name or "Sorted Place", address))
    message["To"] = email
    # One-click unsubscribe stops the first event's reminder; the email offers each.
    stop = context["stop"](items[0])
    message["List-Unsubscribe"] = f"<{stop}>"
    message.set_content(_env.get_template("reminder.txt").render(**context))
    message.add_alternative(_env.get_template("reminder.html").render(**context), subtype="html")
    return message


def run(session: Session, *, dry_run: bool = False, now: datetime | None = None) -> Summary:
    summary = Summary()
    if not service.configured():
        summary.notes.append("no service role key: nothing read")
        return summary
    now = now or datetime.now(timezone.utc)
    local_hour = now.astimezone(listing.DUBLIN).hour
    quiet = not (SEND_FROM_HOUR <= local_hour < SEND_UNTIL_HOUR)
    rows = service.event_reminders(now - timedelta(days=2))
    summary.reminders = len(rows)

    by_email: dict[str, list[Item]] = {}
    for row in rows:
        try:
            item, finished = item_for(session, row, now)
        except Exception:  # noqa: BLE001 - one reminder must not stop the rest
            logger.exception("could not build an event reminder")
            summary.failed += 1
            continue
        if finished:
            summary.finished += 1
            if not dry_run:
                try:
                    service.delete_reminder(row["id"])
                except service.ServiceError:
                    logger.warning("could not remove a finished reminder", exc_info=True)
            continue
        if item is not None:
            by_email.setdefault(row["email"], []).append(item)
    # Reminders whose event is long past and so never read above.
    if not dry_run:
        try:
            for old in service.finished_reminders(now - timedelta(days=2)):
                service.delete_reminder(old["id"])
                summary.finished += 1
        except service.ServiceError:
            logger.warning("could not clear old reminders", exc_info=True)

    if quiet:
        if by_email:
            summary.notes.append(f"{len(by_email)} waiting for the morning (quiet hours)")
        return summary
    sending = bool(settings.smtp_host) and not dry_run
    if not settings.smtp_host and not dry_run:
        summary.notes.append("no SMTP host set: reporting only, nothing sent")

    server = None
    try:
        for email, items in list(by_email.items())[:MAX_EMAILS]:
            items.sort(key=lambda i: i.starts)
            message = compose(email, items)
            if not sending:
                summary.sent += 1
                logger.info("would send %r", message["Subject"])
                continue
            try:
                server = server or _smtp()
                server.send_message(message)
                for item in items:
                    columns = {"last_sent_at": now.isoformat()}
                    if item.day_before:
                        columns["day_before_sent"] = True
                    service.update_reminder(item.row["id"], **columns)
                summary.sent += 1
            except (smtplib.SMTPException, OSError, service.ServiceError):
                logger.exception("could not send an event reminder")
                summary.failed += 1
                server = None
    finally:
        if server is not None:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
    return summary
