"""Vercel entry point for the web finder.

Vercel serves a Python function by importing an ASGI `app` from `api/`. The package
lives under `src/`, which is not on the import path of a function that is not installed
as a distribution, so it is added here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Pin the deployment to the committed snapshot before anything reads the environment.
#
# This function only ever reads: no route in `jobfinder.web` writes, and the writers are
# the two GitHub Actions workflows. So there is no such thing as a correct database URL
# here, and honouring one is a liability rather than a feature — an inherited
# JOBFINDER_DATABASE_URL pointing at hosted Postgres is what put every page load on the
# network, spent the egress allowance that ran out, and then made a database outage
# indistinguishable from the site being down.
#
# Setting it here rather than deleting it in the dashboard means the deployment cannot be
# misconfigured back into that state by a stray environment variable. `settings` reads
# the environment once at import, so this has to happen before the app is imported.
SNAPSHOT = ROOT / "data" / "jobfinder.db"
if SNAPSHOT.exists():
    os.environ["JOBFINDER_DATABASE_URL"] = (
        f"sqlite:///file:{SNAPSHOT}?mode=ro&immutable=1&uri=true"
    )

from jobfinder.web.app import app  # noqa: E402

__all__ = ["app"]
