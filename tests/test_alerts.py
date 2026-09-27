"""Email alerts: who is due, what is new, what the email says, and that it is sent once."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jobfinder.alerts import digest, service
from jobfinder.core.config import settings

NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def _row(**extra) -> dict:
    return {"user_id": "u1", "email": "aoife@example.com", "internships": False, "graduate": False,
            "jobs": False, "frequency": "daily", "token": str(uuid.uuid4()), "last_sent_at": None,
            "created_at": (NOW - timedelta(days=30)).isoformat(), **extra}


def test_who_is_due():
    assert digest.is_due(_row(), NOW)
    assert not digest.is_due(_row(last_sent_at=(NOW - timedelta(hours=6)).isoformat()), NOW)
    assert digest.is_due(_row(last_sent_at=(NOW - timedelta(hours=21)).isoformat()), NOW)
    weekly = _row(frequency="weekly", last_sent_at=(NOW - timedelta(days=3)).isoformat())
    assert not digest.is_due(weekly, NOW)


def test_new_means_first_seen_since_and_not_an_old_advert_newly_read():
    since = NOW - timedelta(days=1)
    item = lambda first, posted: {"job": SimpleNamespace(first_seen_at=first, posted_at=posted)}
    assert digest._is_new(item(NOW, NOW), since)
    assert digest._is_new(item(NOW, None), since)
    assert not digest._is_new(item(since - timedelta(hours=1), None), since)
    # A newly crawled employer's months-old advert is not new.
    assert not digest._is_new(item(NOW, NOW - timedelta(days=90)), since)


def test_the_subject_says_what_is_new():
    s = lambda n, noun: SimpleNamespace(count=n, noun=noun)
    assert digest.subject([s(1, "internship")]) == "1 new internship in Dublin"
    assert digest.subject([s(3, "graduate programme"), s(12, "job")]) == \
        "3 new graduate programmes and 12 new jobs in Dublin"


def test_sections_list_new_jobs_once_from_the_sites_own_search():
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    row = _row(graduate=True, jobs=True)
    sections = digest.sections_for(row, {"fields": ["software-engineering"], "years": None}, since)
    if not sections:
        pytest.skip("no jobs in this database")
    ids = [i["job"].id for s in sections for i in s.items]
    assert len(ids) == len(set(ids)), "a job is listed in one section only"
    assert all(s.more_url.startswith(settings.site_url) for s in sections)
    # A jobs alert without saved fields sends nothing for jobs.
    assert not [s for s in digest.sections_for(_row(jobs=True), {}, since) if s.key == "jobs"]


def test_the_email_carries_one_click_unsubscribe_and_no_em_dashes(monkeypatch):
    monkeypatch.setattr(settings, "smtp_from", "Sorted Place <alerts@example.com>")
    job = SimpleNamespace(id=7, title="Graduate Engineer — Payments")
    section = digest.Section("graduate", "New graduate programmes", "graduate programme",
                             [{"job": job, "company": "Stripe", "salary": None, "mode": None,
                               "experience": None, "place": ""}], "https://x/?f=a")
    row = _row(graduate=True)
    message = digest.compose(row, [section])
    assert message["To"] == "aoife@example.com"
    assert row["token"] in message["List-Unsubscribe"]
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    html = message.get_body(("html",)).get_content()
    text = message.get_body(("plain",)).get_content()
    assert "/jobs/7" in html and "/jobs/7" in text
    assert "Stop all alerts" in text and "stop all alerts" in html
    for part in (html.replace(job.title, ""), text.replace(job.title, "")):
        assert "—" not in part


def test_run_sends_what_is_due_marks_it_and_skips_empty_digests(monkeypatch):
    rows = [_row(user_id="a", graduate=True), _row(user_id="b", internships=True),
            _row(user_id="c", jobs=True, last_sent_at=(NOW - timedelta(hours=2)).isoformat())]
    sent, marked = [], []

    class Server:
        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass

    job = SimpleNamespace(id=1, title="Graduate Analyst")
    fake_section = digest.Section("graduate", "New graduate programmes", "graduate programme",
                                  [{"job": job, "company": "Citi", "salary": None, "mode": None,
                                    "experience": None, "place": ""}], "https://x")
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "subscribers", lambda: rows)
    monkeypatch.setattr(service, "profiles", lambda ids: {})
    monkeypatch.setattr(service, "mark_sent", lambda uid, when: marked.append(uid))
    monkeypatch.setattr(digest, "sections_for",
                        lambda row, profile, since: [fake_section] if row["graduate"] else [])
    monkeypatch.setattr(digest, "_smtp", lambda: Server())
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")

    summary = digest.run(now=NOW)
    assert (summary.subscribers, summary.due, summary.sent, summary.empty) == (3, 2, 1, 1)
    assert marked == ["a"] and sent[0]["To"] == "aoife@example.com"


def test_run_without_a_mail_server_only_reports(monkeypatch):
    monkeypatch.setattr(service, "configured", lambda: True)
    monkeypatch.setattr(service, "subscribers", lambda: [_row(graduate=True)])
    monkeypatch.setattr(service, "profiles", lambda ids: {})
    monkeypatch.setattr(service, "mark_sent", lambda *a: pytest.fail("nothing was sent"))
    monkeypatch.setattr(digest, "sections_for", lambda *a: [SimpleNamespace(count=1, noun="job")])
    monkeypatch.setattr(digest, "compose", lambda row, sections: {"Subject": "x"})
    monkeypatch.setattr(settings, "smtp_host", "")
    assert "nothing sent" in str(digest.run(now=NOW))


def test_run_without_the_service_key_reads_nothing(monkeypatch):
    monkeypatch.setattr(service, "configured", lambda: False)
    assert "no service role key" in str(digest.run(now=NOW))
