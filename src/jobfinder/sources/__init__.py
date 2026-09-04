"""Source adapters.

Importing this package's modules registers each adapter in the shared registry.
Adapters are grouped by tier:

* Tier 1  - standard ATS platforms with public JSON APIs (the bulk of coverage)
* Tier 1b - `bespoke/`, one module per large employer running its own careers platform
"""

from __future__ import annotations

_loaded = False


def load_adapters() -> None:
    """Import every adapter module so registration side effects run."""
    global _loaded
    if _loaded:
        return

    # Tier 1: standard ATS platforms.
    from jobfinder.sources import (  # noqa: F401
        ashby,
        greenhouse,
        lever,
        workable,
        workday,
    )

    # Tier 1b: employers on bespoke platforms.
    from jobfinder.sources.bespoke import amazon, google  # noqa: F401

    _loaded = True
