"""Keep the `events` table up to date.

The same promise as the jobs: nothing is ever deleted. Each run adds the careers events
it has not seen before and refreshes the ones it has (a moved start time, a new venue,
a cancellation). An event that has finished stays, and the page lists it under
Completed. One site failing leaves its events exactly as they were.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import Event
from jobfinder.events import relevance, sources
from jobfinder.sources.base import build_client

logger = logging.getLogger(__name__)

# Copied from a listing onto the stored event on every sighting.
_FIELDS = ("url", "title", "summary", "organizer", "image_url", "starts_at", "ends_at",
           "has_time", "venue", "town", "region", "is_online", "is_free", "cancelled")


@dataclass
class Summary:
    read: dict[str, int] = field(default_factory=dict)
    kept: int = 0
    added: int = 0
    updated: int = 0
    failed: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        sites = ", ".join(f"{name} {count}" for name, count in self.read.items())
        text = f"events: read {sites}; {self.kept} careers events, {self.added} new, {self.updated} refreshed"
        return text + (f"; failed: {', '.join(self.failed)}" if self.failed else "")


def keep(raw: sources.RawEvent) -> str | None:
    """The kind of careers event this is, or None to leave it out."""
    if not raw.title:
        return None
    if not relevance.is_careers_event(raw.title, raw.summary,
                                      career_tag="Career" in raw.tags or raw.source == "gradireland",
                                      tech_hint=raw.tech_hint):
        return None
    return relevance.kind_of(raw.title, raw.summary, source=raw.source, tech_hint=raw.tech_hint)


def store(session: Session, raws: list[sources.RawEvent], summary: Summary,
          now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    seen: dict[tuple[str, str], tuple[sources.RawEvent, str]] = {}
    for raw in raws:
        kind = keep(raw)
        if kind:
            seen[(raw.source, raw.source_id)] = (raw, kind)
    summary.kept = len(seen)
    # A rule added since an event was stored applies to it too: an event the block list
    # now rules out is hidden (as cancelled), never deleted, and comes back if the rule
    # is relaxed and the listing is seen again.
    for event in session.execute(select(Event).where(Event.cancelled.is_(False))).scalars():
        if relevance.is_blocked(event.title):
            event.cancelled = True
    if not seen:
        return
    existing = {
        (e.source, e.source_id): e
        for e in session.execute(select(Event).where(
            Event.source.in_({source for source, _ in seen}))).scalars()
    }
    for key, (raw, kind) in seen.items():
        event = existing.get(key)
        if event is None:
            event = Event(source=raw.source, source_id=raw.source_id, first_seen_at=now)
            session.add(event)
            summary.added += 1
        else:
            summary.updated += 1
        for name in _FIELDS:
            setattr(event, name, getattr(raw, name))
        event.kind = kind
        event.last_seen_at = now


def run(session: Session, readers: dict | None = None) -> Summary:
    summary = Summary()
    raws: list[sources.RawEvent] = []
    with build_client() as client:
        for name, reader in (readers or sources.READERS).items():
            try:
                found = reader(client)
            except Exception:  # noqa: BLE001 - one site must not stop the others
                logger.exception("events: %s failed", name)
                summary.failed.append(name)
                continue
            summary.read[name] = len(found)
            raws.extend(found)
    store(session, raws, summary)
    return summary
