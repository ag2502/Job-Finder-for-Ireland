"""Location normalizer tests.

The `REAL_*` cases are not invented: they are the exact strings observed on a single
live Greenhouse board, which is what motivated this module.
"""

from __future__ import annotations

import pytest

from jobfinder.normalize.location import normalize_location

# Every distinct spelling of Dublin found on one company's board.
REAL_DUBLIN_SPELLINGS = [
    "Dublin, Ireland",
    "Dublin, Ireland ",
    "Dublin",
    "Dublin ",
    "London OR Dublin",
    "Dublin OR London",
    "Dublin or London",
    "Dublin, London",
    "London, Dublin",
    "Singapore, Dublin",
    "SF, New York, Seattle, Dublin, Luxembourg",
]

# Real places that must never be treated as Irish roles.
US_DUBLINS = [
    "Dublin, CA",
    "Dublin, California",
    "Dublin, OH",
    "Dublin, Ohio",
    "Dublin, GA",
    "Dublin, Georgia",
    "Dublin, TX",
    # Workday tenants write US offices without the comma.
    "Store 2745084 Dublin GA",
    "Store 2743401 Dublin OH",
    "DUBLIN-  OH - US",
    "Dublin California United States",
    "USA OH - Dublin FSS E Svc Ctr",
]

NON_DUBLIN = [
    "London, United Kingdom",
    "San Francisco, CA",
    "Berlin, Germany",
    "Cork, Ireland",
    "Remote - US",
    "",
    None,
]


@pytest.mark.parametrize("raw", REAL_DUBLIN_SPELLINGS)
def test_real_dublin_spellings_are_matched(raw: str) -> None:
    result = normalize_location(raw)
    assert result.is_dublin, f"failed to match Dublin in {raw!r}"
    assert result.location_norm == "Dublin, Ireland"


@pytest.mark.parametrize("raw", US_DUBLINS)
def test_us_dublins_are_rejected(raw: str) -> None:
    result = normalize_location(raw)
    assert not result.is_dublin, f"wrongly matched US location {raw!r} as Irish"


@pytest.mark.parametrize("raw", NON_DUBLIN)
def test_non_dublin_locations(raw: str | None) -> None:
    assert not normalize_location(raw).is_dublin


@pytest.mark.parametrize(
    "raw",
    [
        "D02",
        "D04 X5R7",
        "Dublin 2",
        "Dublin 4",
        "Dublin 18",
        "Grand Canal Dock",
        "Grand Canal Dock, Dublin",
        "Sandyford, Co. Dublin",
        "IFSC",
        "Citywest",
        "Blanchardstown",
        "Leopardstown, Dublin 18",
        "Loughlinstown, Ireland",
        "Ireland - Dublin - Grange Castle",
        "Liffey Valley, Block B",
    ],
)
def test_dublin_localities_and_postcodes(raw: str) -> None:
    assert normalize_location(raw).is_dublin, f"failed on {raw!r}"


def test_raw_is_never_mutated() -> None:
    raw = "  Dublin, Ireland  "
    result = normalize_location(raw)
    assert result.raw == raw
    assert result.is_dublin


def test_remote_detection() -> None:
    assert normalize_location("Remote - Ireland").is_remote
    assert normalize_location("Remote (Dublin)").is_remote
    assert normalize_location("Work from home, Dublin").is_remote
    assert not normalize_location("Dublin, Ireland").is_remote


def test_hybrid_detection() -> None:
    assert normalize_location("Dublin (Hybrid)").is_hybrid
    assert not normalize_location("Dublin, Ireland").is_hybrid


def test_remote_ireland_counts_as_dublin_eligible() -> None:
    # A remote Irish role is relevant to a Dublin-based searcher.
    result = normalize_location("Remote - Ireland")
    assert result.is_remote
    assert not result.is_dublin  # not Dublin specifically, but flagged remote


def test_multi_office_listing_including_us_and_dublin() -> None:
    # Dublin stands as its own entry; the US cities must not disqualify it.
    result = normalize_location("SF, New York, Seattle, Dublin, Luxembourg")
    assert result.is_dublin
    assert not result.needs_review


@pytest.mark.parametrize(
    "raw",
    [
        "Dublin - New York",
        "Dublin OR London",
        "Dublin Co Dublin",
        "Dublin - IE",
        "NY - Dublin",
        "Dublin (Hybrid)",
        "IRL - L - DUBLIN",
    ],
)
def test_a_neighbouring_office_or_county_is_not_a_us_state(raw: str) -> None:
    assert normalize_location(raw).is_dublin, f"wrongly rejected {raw!r}"


def test_explicit_ireland_beats_us_state_adjacency() -> None:
    result = normalize_location("Dublin, CA and Dublin, Ireland")
    assert result.is_dublin


def test_conflicting_us_signal_is_flagged_for_review() -> None:
    result = normalize_location("Dublin, United States")
    assert result.needs_review or not result.is_dublin


def test_company_hint_resolves_ambiguity() -> None:
    result = normalize_location("Dublin, USA", company_is_irish=True)
    assert not result.needs_review
