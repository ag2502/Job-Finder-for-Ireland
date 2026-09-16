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

#: True when the URL names a SQLite file opened read-only or immutable.
#:
#: The deployed site serves a prebuilt snapshot (see `scripts/export_snapshot.py`) from a
#: filesystem that is read-only anyway, so any write is a bug rather than something to
#: attempt and fail at. `immutable=1` additionally tells SQLite the file cannot change
#: underneath it, which skips locking entirely — correct here because the snapshot is
#: replaced by a deployment, never edited in place.
IS_READ_ONLY = settings.database_url.startswith("sqlite") and (
    "mode=ro" in settings.database_url or "immutable=1" in settings.database_url
)

if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    if "uri=true" in settings.database_url:
        # SQLAlchemy passes the path through to sqlite3 verbatim; without this the
        # `file:...?mode=ro` form is taken as a literal filename and SQLite creates a new
        # empty database with a very strange name instead of opening the snapshot.
        _connect_args["uri"] = True
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
    if IS_READ_ONLY:
        # `create_all` issues DDL even when every table already exists, which fails
        # against a read-only file. The snapshot ships with its schema already built, so
        # there is nothing to create.
        return
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
