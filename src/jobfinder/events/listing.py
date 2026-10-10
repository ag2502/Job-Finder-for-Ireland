"""Careers events arranged for /events: on now, this week, later on, and completed.

Everything here reads; the crawl is the only writer. An event moves between sections by
the clock alone, so the page never needs a crawl to move a finished fair to Completed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from jobfinder.core.models import Event
from jobfinder.events.relevance import KINDS

DUBLIN = ZoneInfo("Europe/Dublin")
# An event with a start time and no end is taken to run this long; one with only a date
# runs to the end of that day.
ASSUMED_LENGTH = timedelta(hours=3)
WEEK = timedelta(days=7)
# Where the same event is listed twice, the organiser's own listing wins.
SOURCE_ORDER = {"gradireland": 0, "eventbrite": 1, "luma": 2, "meetup": 3}
SOURCE_LABELS = {"gradireland": "gradireland", "eventbrite": "Eventbrite", "luma": "Luma",
                 "meetup": "Meetup"}


@dataclass
class EventView:
    event: Event
    starts: datetime          # Irish time
    ends: datetime            # Irish time, never before `starts`
    status: str               # "live", "week", "later" or "done"

    @property
    def id(self) -> int:
        return self.event.id

    @property
    def key(self) -> str:
        """What a reminder is kept against: stable across snapshot rebuilds."""
        return f"{self.event.source}:{self.event.source_id}"

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.event.kind, "Event")

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.event.source, self.event.source.title())

    @property
    def multi_day(self) -> bool:
        return self.ends.date() > self.starts.date() and (self.ends - self.starts) > timedelta(hours=24)

    @property
    def time_label(self) -> str:
        """"10:00 to 12:30", "All day", or "22 Sep to 20 Oct" for a run of days."""
        if self.multi_day:
            last = self.ends - timedelta(seconds=1) if not self.event.has_time else self.ends
            return f"{self.starts:%-d %b} to {last:%-d %b}"
        if not self.event.has_time:
            return "All day"
        if self.event.ends_at:
            return f"{self.starts:%H:%M} to {self.ends:%H:%M}"
        return f"From {self.starts:%H:%M}"

    @property
    def date_label(self) -> str:
        return f"{self.starts:%a %-d %b %Y}"

    @property
    def where(self) -> str:
        if self.event.is_online:
            return "Online"
        parts = [p for p in (self.event.venue, self.event.town) if p]
        if self.event.region and not any(self.event.region.lower() in p.lower() for p in parts):
            parts.append(self.event.region)
        return ", ".join(dict.fromkeys(parts)) or "Ireland, place not stated"

    @property
    def place(self) -> str:
        """The short form for a card: the county, or Online."""
        if self.event.is_online:
            return "Online"
        return self.event.region or self.event.town or "Ireland"

    def countdown(self, now: datetime) -> str:
        """"On now", "Today, 18:00", "Tomorrow", "In 5 days", "Finished 3 days ago"."""
        local_now = now.astimezone(DUBLIN)
        if self.status == "live":
            return "On now"
        if self.status == "done":
            days = (local_now.date() - self.ends.date()).days
            return "Finished today" if days <= 0 else (
                "Finished yesterday" if days == 1 else f"Finished {days} days ago")
        days = (self.starts.date() - local_now.date()).days
        if days <= 0:
            return f"Today, {self.starts:%H:%M}" if self.event.has_time else "Today"
        if days == 1:
            return "Tomorrow"
        if days < 14:
            return f"In {days} days"
        if days < 60:
            return f"In {days // 7} weeks"
        return f"In {days // 30} months"


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DUBLIN)


def span(event: Event) -> tuple[datetime, datetime]:
    starts = _local(event.starts_at)
    if event.ends_at:
        ends = _local(event.ends_at)
    elif event.has_time:
        ends = starts + ASSUMED_LENGTH
    else:
        ends = starts + timedelta(days=1)
    return starts, max(ends, starts)


def status_of(starts: datetime, ends: datetime, now: datetime) -> str:
    if ends <= now:
        return "done"
    if starts <= now:
        return "live"
    if starts - now <= WEEK:
        return "week"
    return "later"


def _same_event(event: Event, starts: datetime) -> tuple:
    words = re.sub(r"[^a-z0-9 ]+", " ", event.title.casefold()).split()
    return (" ".join(words), starts.date())


def load(session: Session, now: datetime | None = None) -> list[EventView]:
    """Every stored event, the same event listed twice counted once, oldest first.

    A snapshot built before events existed has no table; that reads as no events rather
    than an error, until the next crawl ships one.
    """
    now = now or datetime.now(timezone.utc)
    try:
        rows = session.execute(select(Event).where(Event.cancelled.is_(False))).scalars().all()
    except OperationalError:
        return []
    chosen: dict[tuple, EventView] = {}
    for event in sorted(rows, key=lambda e: SOURCE_ORDER.get(e.source, 9)):
        starts, ends = span(event)
        view = EventView(event, starts, ends, status_of(starts, ends, now))
        chosen.setdefault(_same_event(event, starts), view)
    return sorted(chosen.values(), key=lambda v: (v.starts, v.event.title))


def get(session: Session, event_id: int, now: datetime | None = None) -> EventView | None:
    now = now or datetime.now(timezone.utc)
    try:
        event = session.get(Event, event_id)
    except OperationalError:
        return None
    if event is None:
        return None
    starts, ends = span(event)
    return EventView(event, starts, ends, status_of(starts, ends, now))


def by_key(session: Session, key: str, now: datetime | None = None) -> EventView | None:
    source, _, source_id = key.partition(":")
    now = now or datetime.now(timezone.utc)
    try:
        event = session.execute(select(Event).where(
            Event.source == source, Event.source_id == source_id)).scalar_one_or_none()
    except OperationalError:
        return None
    if event is None:
        return None
    starts, ends = span(event)
    return EventView(event, starts, ends, status_of(starts, ends, now))


def months(views: list[EventView]) -> list[tuple[str, list[EventView]]]:
    """Later-on events under a heading per month: "November 2026"."""
    out: dict[str, list[EventView]] = {}
    for view in views:
        out.setdefault(f"{view.starts:%B %Y}", []).append(view)
    return list(out.items())


def calendar(views: list[EventView], today: date, count: int = 4) -> list[dict]:
    """Month grids from this month on, each day carrying how many events start on it."""
    starts_on: dict[date, list[EventView]] = {}
    for view in views:
        if view.status != "done":
            starts_on.setdefault(view.starts.date(), []).append(view)
    out = []
    year, month = today.year, today.month
    for _ in range(count):
        first = date(year, month, 1)
        lead = first.weekday()     # Monday first, as Irish calendars are
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        days = []
        for n in range((nxt - first).days):
            day = first + timedelta(days=n)
            here = starts_on.get(day, [])
            days.append({
                "date": day, "n": len(here), "past": day < today, "today": day == today,
                "kinds": sorted({v.event.kind for v in here}),
            })
        out.append({"label": f"{first:%B %Y}", "key": f"{first:%Y-%m}", "lead": lead, "days": days,
                    "total": sum(d["n"] for d in days)})
        year, month = nxt.year, nxt.month
    return out


def ics(view: EventView, page_url: str) -> str:
    """The event as a calendar file, for Add to calendar."""
    def stamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def text(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    event = view.event
    if event.has_time:
        when = [f"DTSTART:{stamp(view.starts)}", f"DTEND:{stamp(view.ends)}"]
    else:
        last = view.ends if view.multi_day else view.starts + timedelta(days=1)
        when = [f"DTSTART;VALUE=DATE:{view.starts:%Y%m%d}", f"DTEND;VALUE=DATE:{last:%Y%m%d}"]
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sorted Place//Events//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "BEGIN:VEVENT",
        f"UID:{view.key.replace(':', '-')}@sorted-place",
        f"DTSTAMP:{stamp(datetime.now(timezone.utc))}",
        *when,
        f"SUMMARY:{text(event.title)}",
        f"LOCATION:{text(view.where)}",
        f"DESCRIPTION:{text((event.summary or '') + chr(10) + chr(10) + 'Details and registration: ' + event.url)}",
        f"URL:{page_url}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    # Lines over 75 octets fold onto a continuation line, as the format requires.
    folded = []
    for line in lines:
        raw = line.encode()
        while len(raw) > 74:
            cut = 74
            while cut and (raw[cut] & 0xC0) == 0x80:
                cut -= 1
            folded.append(raw[:cut].decode())
            raw = b" " + raw[cut:]
        folded.append(raw.decode())
    return "\r\n".join(folded) + "\r\n"
