"""Engine and session management."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from jobfinder.core.config import settings
from jobfinder.core.models import Base

logger = logging.getLogger(__name__)

_connect_args = {}
_engine_kwargs = {}

if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
else:
    # Hosted Postgres sits behind a connection pooler that closes connections it
    # considers stale, and this pipeline holds them for a long time — a detection sweep
    # runs for the better part of an hour. Without these, the first query after an idle
    # stretch fails with "server closed the connection unexpectedly" and takes the whole
    # transaction with it.
    #
    # `pool_pre_ping` costs one cheap round-trip per checkout and turns a dead connection
    # into a transparent reconnect. `pool_recycle` retires connections before the pooler
    # decides to.
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"] = 280

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    future=True,
    **_engine_kwargs,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def wait_for_database(attempts: int = 5, delay: float = 2.0) -> None:
    """Ping the database, retrying while it wakes.

    Serverless Postgres suspends an idle compute and resumes it on the next connection.
    The resume takes a few seconds, and the connection that triggers it can be dropped
    part-way through - so the *first* statement of a run fails with "server closed the
    connection unexpectedly" even though the database is healthy a second later.

    `pool_pre_ping` does not cover this. It validates a connection being handed out of
    the pool; here the failure happens on a connection that was fine when checked out
    and died mid-query while the compute came up.

    So every entry point pings first and absorbs the cold start once, rather than each
    command growing its own retry. Backoff is linear and short: a resume takes seconds,
    and if five tries over half a minute cannot reach it, the database is genuinely down
    and failing loudly is the right answer.
    """
    if settings.database_url.startswith("sqlite"):
        return

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            if attempt > 1:
                logger.info("database ready after %d attempts", attempt)
            return
        except OperationalError as exc:
            last = exc
            # The dead connection must not go back into the pool to be handed out again.
            engine.dispose()
            if attempt < attempts:
                logger.warning(
                    "database not ready (attempt %d/%d); retrying", attempt, attempts
                )
                time.sleep(delay * attempt)

    raise RuntimeError(
        f"could not reach the database after {attempts} attempts: {last}"
    ) from last


def init_db() -> None:
    wait_for_database()
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
