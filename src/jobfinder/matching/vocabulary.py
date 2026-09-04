"""Access to the learned corpus vocabulary.

Building the vocabulary reads every advert, so it is done once and cached — on disk
between runs, and in memory within a process. Callers just ask for `get()`.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from jobfinder.core.db import session_scope
from jobfinder.core.models import JobPosting, JobStatus
from jobfinder.matching import corpus

logger = logging.getLogger(__name__)

_cached: corpus.Vocabulary | None = None


def build_from_database() -> corpus.Vocabulary:
    with session_scope() as session:
        documents = [
            f"{job.title}\n{job.description or ''}"
            for job in session.execute(
                select(JobPosting).where(JobPosting.status == JobStatus.ACTIVE)
            ).scalars()
        ]
    return corpus.build(documents)


def refresh() -> corpus.Vocabulary:
    """Rebuild from the current corpus and persist. Called after a crawl."""
    global _cached
    vocabulary = build_from_database()
    corpus.save(vocabulary)
    _cached = vocabulary
    return vocabulary


def get() -> corpus.Vocabulary:
    """The vocabulary, from memory, then disk, then rebuilt as a last resort."""
    global _cached
    if _cached is not None:
        return _cached

    _cached = corpus.load()
    if _cached is None:
        logger.info("no cached vocabulary; building from the database")
        _cached = refresh()
    return _cached


def terms_for(text: str | None) -> set[str]:
    """Read a document against the learned vocabulary."""
    return corpus.extract_terms(text, get())
