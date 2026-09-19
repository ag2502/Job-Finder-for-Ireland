"""Source adapters.

Importing this package's modules registers each adapter in the shared registry.
Adapters are grouped by tier:

* Tier 1  - standard ATS platforms with public JSON APIs (the bulk of coverage)
* Tier 1b - `bespoke/`, one module per large employer running its own careers platform
* Tier 3  - `jsonld`, a generic extractor for any site publishing schema.org JobPosting
            markup. Targets a convention rather than a platform, which is what lets it
            reach employers no adapter will ever be written for.
* Tier 4  - `aggregators/`, licensed third-party indexes. Broadest reach, lowest
            quality, and the only tier where one source carries many employers.
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
        bamboohr,
        breezy,
        candidatemanager,
        careers_html,
        eightfold,
        greenhouse,
        hirehive,
        icims,
        jsonld,
        lever,
        occupop,
        oleeo,
        oracle_recruiting,
        personio,
        pinpoint,
        recruitee,
        smartrecruiters,
        successfactors,
        teamtailor,
        workable,
        workday,
    )

    # Tier 1b: employers on bespoke platforms.
    from jobfinder.sources.bespoke import amazon, google  # noqa: F401

    # Tier 3 is imported above alongside Tier 1: `jsonld` is addressed by careers URL
    # rather than by platform slug, but it registers the same way.

    # Tier 4: licensed aggregators, which carry many employers under one source.
    from jobfinder.sources.aggregators import adzuna  # noqa: F401

    _loaded = True
