"""Vercel entry point for the web finder.

Vercel serves a Python function by importing an ASGI `app` from `api/`. The package
lives under `src/`, which is not on the import path of a function that is not installed
as a distribution, so it is added here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobfinder.web.app import app  # noqa: E402

__all__ = ["app"]
