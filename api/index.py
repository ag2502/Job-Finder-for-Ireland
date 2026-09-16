"""Vercel entry point for the web finder.

Vercel serves a Python function by importing an ASGI `app` from `api/`. The package lives
under `src/`, which is not on the import path of a function that is not installed as a
distribution, so it is added here.

This module also pins the deployment to the committed snapshot. The function only ever
reads — no route in `jobfinder.web` writes, and the writers are the two GitHub Actions
workflows — so there is no correct database URL for it to inherit, and honouring one is a
liability: an inherited JOBFINDER_DATABASE_URL pointing at hosted Postgres put every page
load on the network, spent the egress allowance that ran out, and then made a database
outage indistinguishable from the site being down.

The snapshot is located by searching rather than by assuming one path. A bundled
function's layout is not the repository's: `__file__` and the working directory need not
share a root, so a single guessed path is the kind of thing that works locally and
silently falls back to the network in production.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_CANDIDATES = [
    ROOT / "data" / "jobfinder.db",
    Path.cwd() / "data" / "jobfinder.db",
    Path("/var/task/data/jobfinder.db"),
]

SNAPSHOT = next((p for p in _CANDIDATES if p.exists()), None)

if SNAPSHOT is not None:
    os.environ["JOBFINDER_DATABASE_URL"] = (
        f"sqlite:///file:{SNAPSHOT}?mode=ro&immutable=1&uri=true"
    )
else:
    # Falling through to a network database is the failure this file exists to prevent,
    # so drop the inherited URL instead. The app then resolves its own default and the
    # pages fail fast and locally rather than hanging on a connection that cannot work.
    os.environ.pop("JOBFINDER_DATABASE_URL", None)

from jobfinder.web.app import app  # noqa: E402


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Report how the deployment resolved its data, without touching the network.

    Added because the deployed function's behaviour could not be explained from outside:
    some routes failed instantly and others hung for the full database timeout. Guessing
    from response times is slower than asking.
    """
    from sqlalchemy import func, select

    from jobfinder.core.config import settings
    from jobfinder.core.db import IS_READ_ONLY, session_scope
    from jobfinder.core.models import JobPosting

    info: dict = {
        "snapshot_found": SNAPSHOT is not None,
        "snapshot_path": str(SNAPSHOT) if SNAPSHOT else None,
        "candidates": {str(p): p.exists() for p in _CANDIDATES},
        "cwd": str(Path.cwd()),
        "entry_file": str(Path(__file__).resolve()),
        "url_scheme": settings.database_url.split(":")[0],
        "read_only": IS_READ_ONLY,
    }
    try:
        with session_scope() as s:
            info["active_jobs"] = s.execute(
                select(func.count()).select_from(JobPosting)
            ).scalar_one()
    except Exception as exc:  # the whole point is to see this text
        info["query_error"] = f"{type(exc).__name__}: {exc}"[:400]
    return info


__all__ = ["app"]
