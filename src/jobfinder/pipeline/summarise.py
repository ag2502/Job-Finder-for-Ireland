"""Summarise new adverts in three short lines, once each, after a crawl.

What the job is, what it asks for, and the tools it names: enough to decide whether to
read the whole advert. Written by the same free models CV tailoring uses (tailor/llm.py)
and kept in the crawler's database, so an advert costs one request ever however many
crawls it survives.

Three rules keep a summary honest:

* The model is told to use only what the advert says, and every tool it lists must
  appear, word for word, in the advert's text; any that does not is dropped.
* A summary is only ever a way into the advert. The panel says it was written by a model
  and puts the employer's own text straight under it.
* Nothing here can hold up a crawl. The step runs after the crawl has been saved, stops
  at its time budget, and an unconfigured or unavailable model simply leaves adverts for
  the next run.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import AdvertSummary, JobPosting, JobStatus
from jobfinder.matching.rank import advert_hash
from jobfinder.tailor import llm

logger = logging.getLogger(__name__)

# Adverts per request. Five keeps a request well inside every free model's context and
# output limits while spending a fifth of the requests one each would.
BATCH = 5
# Below this there is nothing to summarise: the advert is a title and a link.
MIN_TEXT = 300
# What is sent of each advert. Requirements are nearly always in the first few thousand
# characters; the rest is benefits and legal text.
MAX_TEXT = 6000
MAX_SENTENCE = 220
MAX_TOOLS = 6
# Free tiers count requests a minute; a short pause between batches stays under them.
PAUSE_SECONDS = 4.0

SYSTEM = (
    "You summarise job adverts for people job hunting in Dublin. For each advert, write "
    "two plain sentences and a list, using only what that advert says:\n"
    "- does: what the person in this job will do, in at most 25 words.\n"
    "- needs: the experience, qualifications or skills the advert says are required, in "
    "at most 25 words. Leave out anything it calls a nice to have.\n"
    "- tools: up to 6 named tools, languages, platforms or certifications the advert "
    "mentions, spelled exactly as it spells them. An empty list if it names none.\n"
    "Never add a fact, figure, salary, benefit or requirement the advert does not state. "
    "No hype, no marketing words, no em dashes. Write in British English. Start each "
    "sentence with a verb or a noun, not with 'You' or 'The role'."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "adverts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "does": {"type": "string"},
                    "needs": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "does", "needs", "tools"],
            },
        }
    },
    "required": ["adverts"],
}


@dataclass
class Result:
    written: int = 0
    skipped: int = 0
    failed_batches: int = 0
    reason: str = ""

    def __str__(self) -> str:
        text = f"summarised {self.written} adverts ({self.skipped} dropped as unusable)"
        if self.failed_batches:
            text += f"; {self.failed_batches} batches got no answer"
        return text + (f"; {self.reason}" if self.reason else "")


def advert_key(job: JobPosting) -> str:
    """The key a summary is stored under: the same text ranking reads."""
    return advert_hash(f"{job.title}\n{job.description or ''}")


def _sentence(value: object) -> str:
    text = " ".join(str(value or "").split())
    # House style: no em dashes, and no hyphen standing in for one.
    text = re.sub(r"\s*—\s*|\s+[–-]\s+", ", ", text).strip(" ,")
    if len(text) > MAX_SENTENCE:
        text = text[:MAX_SENTENCE].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return text


def clean(answer: dict, advert_text: str) -> dict | None:
    """A model's summary made safe to show, or None when it is not usable."""
    does, needs = _sentence(answer.get("does")), _sentence(answer.get("needs"))
    if len(does) < 12 or len(needs) < 12:
        return None
    haystack = advert_text.casefold()
    tools: list[str] = []
    for tool in answer.get("tools") or []:
        name = " ".join(str(tool).split())[:40]
        # Only what the advert itself names, and each once.
        if name and name.casefold() in haystack and name.casefold() not in {t.casefold() for t in tools}:
            tools.append(name)
    return {"does": does, "needs": needs, "tools": tools[:MAX_TOOLS]}


def pending(session: Session, limit: int) -> list[JobPosting]:
    """Live Dublin and remote adverts with text and no summary yet, newest first."""
    done = set(session.scalars(select(AdvertSummary.advert_hash)).all())
    rows = session.scalars(
        select(JobPosting)
        .where(
            JobPosting.status == JobStatus.ACTIVE,
            JobPosting.consecutive_misses == 0,
            JobPosting.is_dublin.is_(True) | JobPosting.is_remote.is_(True),
        )
        .order_by(JobPosting.is_dublin.desc(), JobPosting.first_seen_at.desc())
    ).all()
    out, seen = [], set()
    for job in rows:
        if len(job.description or "") < MIN_TEXT:
            continue
        key = advert_key(job)
        if key in done or key in seen:
            continue
        seen.add(key)
        out.append(job)
        if len(out) >= limit:
            break
    return out


def summarise(session: Session, *, limit: int = 150, minutes: float = 8.0) -> Result:
    """Write summaries for up to `limit` adverts, stopping at the time budget."""
    result = Result()
    if not llm.available():
        result.reason = "no model configured (JOBFINDER_GEMINI_API_KEY or JOBFINDER_GROQ_API_KEY)"
        return result
    stop = time.monotonic() + minutes * 60
    jobs = pending(session, limit)
    for start in range(0, len(jobs), BATCH):
        if time.monotonic() > stop - 45:
            result.reason = "stopped at the time budget; the rest wait for the next run"
            break
        batch = jobs[start:start + BATCH]
        texts = {str(i): (job.description or "")[:MAX_TEXT] for i, job in enumerate(batch)}
        request = [
            {"id": key, "title": job.title, "text": texts[key]}
            for key, job in zip(texts, batch)
        ]
        try:
            answer, model = llm.ask(
                SYSTEM, json.dumps(request, ensure_ascii=False), SCHEMA,
                name="advert_summaries", max_tokens=2500,
                deadline=min(stop, time.monotonic() + 90),
            )
        except llm.ModelsUnavailable as exc:
            logger.warning("no summary model answered: %s", exc)
            result.failed_batches += 1
            if result.failed_batches >= 3:
                result.reason = "models unavailable; stopped early"
                break
            continue
        by_id = {str(a.get("id")): a for a in answer.get("adverts") or [] if isinstance(a, dict)}
        for key, job in zip(texts, batch):
            summary = clean(by_id.get(key) or {}, f"{job.title}\n{job.description or ''}")
            if summary is None:
                result.skipped += 1
                continue
            session.merge(AdvertSummary(
                advert_hash=advert_key(job), summary=json.dumps(summary), model=model,
            ))
            result.written += 1
        session.commit()
        time.sleep(PAUSE_SECONDS)
    return result
