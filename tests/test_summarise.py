"""Short advert summaries: written once, only from the advert, and never a blocker."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from jobfinder.core.models import AdvertSummary, JobPosting, JobStatus
from jobfinder.pipeline import summarise as summ
from jobfinder.tailor import llm

ADVERT = (
    "You will build payment APIs in Python and Go, run services on Kubernetes, and "
    "work with product managers on fraud tooling. You need 3 years of backend "
    "experience and strong SQL. PostgreSQL experience is a nice to have. " * 3
)


def _job(session, source, n: int, text: str = ADVERT, dublin: bool = True) -> JobPosting:
    job = JobPosting(
        company_id=source.company_id, source_id=source.id, source_job_id=f"j{n}",
        dedup_key=f"k{n}", title=f"Backend Engineer {n}", description=text,
        url=f"https://example.com/{n}", is_dublin=dublin, is_remote=False,
        status=JobStatus.ACTIVE,
    )
    session.add(job)
    session.flush()
    return job


def test_clean_keeps_only_tools_the_advert_names():
    answer = {
        "does": "Builds payment APIs and fraud tooling with product managers.",
        "needs": "Three years of backend experience and strong SQL.",
        "tools": ["Python", "Go", "Kubernetes", "Rust", "python", "Terraform"],
    }
    out = summ.clean(answer, ADVERT)
    assert out["tools"] == ["Python", "Go", "Kubernetes"]


def test_clean_rejects_an_empty_answer_and_strips_dashes():
    assert summ.clean({"does": "", "needs": "x", "tools": []}, ADVERT) is None
    out = summ.clean({"does": "Builds APIs — and tooling for fraud teams.",
                      "needs": "Backend experience - three years or more.", "tools": []}, ADVERT)
    assert "—" not in out["does"] and " - " not in out["needs"]


def test_summaries_are_written_once_per_advert(session, source, monkeypatch):
    for n in range(7):
        _job(session, source, n)
    _job(session, source, 99, text="Too short.")
    calls = []

    def fake_ask(system, user, schema, **kwargs):
        sent = json.loads(user)
        calls.append(len(sent))
        return {"adverts": [
            {"id": a["id"], "does": "Builds payment APIs and fraud tooling.",
             "needs": "Three years of backend experience and strong SQL.", "tools": ["Python"]}
            for a in sent
        ]}, "fake-model"

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(summ, "PAUSE_SECONDS", 0)

    first = summ.summarise(session, limit=50)
    assert first.written == 7 and calls == [5, 2]
    stored = session.scalars(select(AdvertSummary)).all()
    assert len(stored) == 7 and all(s.model == "fake-model" for s in stored)

    # A second run has nothing left to do: each advert costs one request ever.
    calls.clear()
    assert summ.summarise(session, limit=50).written == 0 and calls == []


def test_without_a_model_it_does_nothing_and_says_why(session, source, monkeypatch):
    _job(session, source, 1)
    monkeypatch.setattr(llm, "available", lambda: False)
    result = summ.summarise(session)
    assert result.written == 0 and "no model configured" in str(result)


def test_a_model_that_never_answers_stops_early_without_failing(session, source, monkeypatch):
    for n in range(20):
        _job(session, source, n)

    def down(*args, **kwargs):
        raise llm.ModelsUnavailable("HTTP 429")

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "ask", down)
    result = summ.summarise(session, limit=50)
    assert result.written == 0 and result.failed_batches == 3


def test_the_panel_shows_a_summary_labelled_as_written_by_a_model(monkeypatch):
    from fastapi.testclient import TestClient

    from jobfinder.core.db import session_scope
    from jobfinder.matching.rank import advert_hash
    from jobfinder.web import app as web

    with session_scope() as s:
        job = s.scalar(select(JobPosting).where(web._is_offerable(), JobPosting.is_dublin.is_(True),
                                                JobPosting.description.is_not(None)).limit(1))
        if job is None:
            pytest.skip("no live job in this database")
        key, job_id = advert_hash(f"{job.title}\n{job.description or ''}"), job.id
    monkeypatch.setattr(web, "_summaries", {key: {
        "does": "Builds the thing.", "needs": "Two years of doing the thing.", "tools": ["Python"],
    }})
    panel = TestClient(web.app).get(f"/jobs/{job_id}", headers={"HX-Request": "true"}).text
    assert "In short" in panel and "Builds the thing." in panel
    assert "by a language model" in panel
