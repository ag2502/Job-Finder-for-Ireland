"""How the results list presents each job: places, ages and match strength."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from jobfinder.matching import rank
from jobfinder.web.app import STRENGTH_CAPS, _age, _short_place, _strength


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Dublin, IRL", ""),
        ("Dublin, County Dublin, IE", ""),
        ("Ireland - Dublin", ""),
        ("Dublin  Ireland", ""),
        ("Dublin, Leinster, Ireland", ""),
        ("Dublin 2, Ireland", "Dublin 2"),
        ("Cork, Ireland; Dublin, Ireland", "Cork"),
        ("Dublin, Ireland (Mountain View)", ""),
        ("Ireland - Dublin - Grange Castle", "Grange Castle"),
        ("CityWest Office", "CityWest Office"),
        (None, ""),
    ],
)
def test_short_place_keeps_only_what_adds_to_dublin(raw, expected):
    assert _short_place(raw) == expected


def test_age_reads_the_way_people_say_it():
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    assert _age(datetime(2026, 9, 27, 1, tzinfo=timezone.utc), now) == "today"
    assert _age(datetime(2026, 9, 26, tzinfo=timezone.utc), now) == "yesterday"
    assert _age(datetime(2026, 9, 20, tzinfo=timezone.utc), now) == "7 days ago"
    assert _age(datetime(2026, 9, 1, tzinfo=timezone.utc), now) == "3 weeks ago"
    assert _age(datetime(2023, 3, 21, tzinfo=timezone.utc), now) == "Mar 2023"
    assert _age(None, now) == ""


def test_strength_splits_a_list_into_fifths():
    levels = [_strength(i, 10) for i in range(10)]
    assert levels == [3, 3, 2, 2, 2, 2, 1, 1, 1, 1]


def test_a_band_caps_how_strong_its_jobs_can_read():
    """Position alone made the top fifth "Strong" even deep into every other graduate
    job open now, which is no match for the fields picked at all."""
    assert _strength(0, 10, cap=STRENGTH_CAPS[rank.TIER_SKILLS]) == 1
    assert _strength(0, 10, cap=STRENGTH_CAPS[rank.TIER_CV]) == 2
    assert _strength(0, 10, cap=STRENGTH_CAPS[rank.TIER_CHOSEN]) == 3
    # A cap never lifts a job above where it ranks.
    assert _strength(9, 10, cap=3) == 1
