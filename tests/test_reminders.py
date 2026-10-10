"""Remind me: an email daily or weekly until an event, always the day before, never
after, one email per person, and nothing at night."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jobfinder.alerts import service
from jobfinder.core.config import settings
from jobfinder.core.models import Event
from jobfinder.events import reminders

# 10:00 Irish time on Saturday 10 October 2026.
NOW = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def _fair(session, days: float = 5, **kw) -> Event:
    event = Event(source="eventbrite", source_id=kw.pop("source_id", "1"),
                  url="https://www.eventbrite.ie/e/1", title=kw.pop("title", "Job and Training Fair"),
                  starts_at=NOW + timedelta(days=days), has_time=True, kind="fair",
                  region="Dublin", venue="Hall", is_online=False, cancelled=False, **kw)
    session.add(event)
    session.flush()
    return event


def _row(**kw) -> dict:
    row = {"id": "r1", "email": "a@example.com", "event_key": "eventbrite:1", "title": "Job and Training Fair",
           "starts_at": (NOW + timedelta(days=5)).isoformat(), "url": "https://www.eventbrite.ie/e/1",
           "frequency": "weekly", "token": "tok", "last_sent_at": None, "day_before_sent": False}
    row.update(kw)
    return row


def test_when_a_reminder_is_due():
    starts = NOW + timedelta(days=5)
    assert reminders.due(_row(), starts, NOW) == "regular"            # the first one confirms it
    sent_yesterday = (NOW - timedelta(days=1)).isoformat()
    assert reminders.due(_row(last_sent_at=sent_yesterday), starts, NOW) is None
    assert reminders.due(_row(last_sent_at=sent_yesterday, frequency="daily"), starts, NOW) == "regular"
    tomorrow = NOW + timedelta(hours=26)
    assert reminders.due(_row(last_sent_at=sent_yesterday), tomorrow, NOW) == "day_before"
    assert reminders.due(_row(day_before_sent=True), tomorrow, NOW) is None
    assert reminders.due(_row(), NOW - timedelta(hours=1), NOW) is None   # already started


def test_a_finished_event_ends_its_reminder(session):
    _fair(session, days=-1)
    item, finished = reminders.item_for(session, _row(), NOW)
    assert item is None and finished


def test_the_email_reads_from_the_freshly_crawled_event(session):
    event = _fair(session, days=1, ends_at=NOW + timedelta(days=1, hours=3))
    item, finished = reminders.item_for(session, _row(), NOW)
    assert not finished and item.day_before and item.countdown == "Tomorrow"
    assert item.page.endswith(f"/events/{event.id}") and item.calendar.endswith("/calendar.ics")
    message = reminders.compose("a@example.com", [item])
    assert message["Subject"] == "Tomorrow: Job and Training Fair"
    body = message.get_body(("plain",)).get_content()
    assert "Stop reminding me:" in body and "/reminders/stop?token=tok" in body
    assert "—" not in message.as_string()


class Server:
    def __init__(self):
        self.sent = []

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        pass


def test_one_email_per_person_and_what_was_sent_is_recorded(session, monkeypatch):
    _fair(session, days=5)
    _fair(session, days=1, source_id="2", title="Graduate Careers Fair")
    rows = [_row(), _row(id="r2", event_key="eventbrite:2", title="Graduate Careers Fair")]
    updates, server = [], Server()
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "event_reminders", lambda since: rows)
    monkeypatch.setattr(service, "finished_reminders", lambda before: [])
    monkeypatch.setattr(service, "update_reminder", lambda rid, **cols: updates.append((rid, cols)))
    monkeypatch.setattr(reminders, "_smtp", lambda: server)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    summary = reminders.run(session, now=NOW)
    assert summary.sent == 1 and len(server.sent) == 1
    assert server.sent[0]["Subject"].startswith("2 events you asked about, the first tomorrow")
    assert dict(updates)["r2"]["day_before_sent"] is True and "day_before_sent" not in dict(updates)["r1"]


def test_nothing_goes_out_at_night(session, monkeypatch):
    _fair(session, days=5)
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "event_reminders", lambda since: [_row()])
    monkeypatch.setattr(service, "finished_reminders", lambda before: [])
    monkeypatch.setattr(reminders, "_smtp", lambda: (_ for _ in ()).throw(AssertionError("sent at night")))
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    summary = reminders.run(session, now=datetime(2026, 10, 10, 2, 0, tzinfo=timezone.utc))
    assert summary.sent == 0 and "quiet hours" in str(summary)


def test_finished_reminders_are_removed(session, monkeypatch):
    _fair(session, days=-2)
    removed = []
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "event_reminders", lambda since: [_row()])
    monkeypatch.setattr(service, "finished_reminders", lambda before: [{"id": "old"}])
    monkeypatch.setattr(service, "delete_reminder", removed.append)
    monkeypatch.setattr(settings, "smtp_host", "")
    summary = reminders.run(session, now=NOW)
    assert removed == ["r1", "old"] and summary.finished == 2
