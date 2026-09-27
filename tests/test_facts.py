"""Facts read from an advert's prose: only what it says, never a guess."""

from __future__ import annotations

import pytest

from jobfinder.normalize.facts import HYBRID, ONSITE, REMOTE, WorkMode, work_mode


@pytest.mark.parametrize("text, expected", [
    ("We offer a hybrid working model (up to 2 days remote).", WorkMode(HYBRID)),
    ("#LI-Hybrid Version 1 has celebrated 30 years.", WorkMode(HYBRID)),
    ("This is a hybrid role requiring in-office collaboration.", WorkMode(HYBRID)),
    ("Location: Hybrid from Dublin or Cork, Ireland", WorkMode(HYBRID)),
    ("London, hybrid (3 days per week in the office).", WorkMode(HYBRID, 3)),
    ("You will work two days per week in our\nDublin office.", WorkMode(HYBRID, 2)),
    ("Expect three days a week onsite.", WorkMode(HYBRID, 3)),
    ("5 days per week in the office.", WorkMode(ONSITE)),
    ("Fully onsite:\nDublin 2/ Dublin 4", WorkMode(ONSITE)),
    ("#LI-Onsite", WorkMode(ONSITE)),
    ("This is a fully remote role.", WorkMode(REMOTE)),
    ("We are remote-first.", WorkMode(REMOTE)),
    ("Remote within Ireland is possible.", WorkMode(REMOTE)),
])
def test_what_the_advert_says(text, expected):
    assert work_mode(text) == expected


@pytest.mark.parametrize("text", [
    "You will design hybrid cloud platforms on AWS and Azure.",
    "Experience with hybrid infrastructure and on-prem networks.",
    "Build a hybrid app in React Native.",
    "The role is a hybrid of engineering and sales.",
    "Shape a hybrid go-to-market motion.",
    "Work with remote teams across Europe.",
    "An office in Dublin 2 with a gym.",
    "",
    None,
])
def test_nothing_stated_is_none(text):
    assert work_mode(text) is None


def test_hybrid_beats_remote_when_both_are_offered():
    assert work_mode("Hybrid / remote working flexibility for all staff.").kind == HYBRID


def test_the_location_flag_counts_as_remote():
    assert work_mode("We build payments.", is_remote=True) == WorkMode(REMOTE)


def test_labels():
    assert WorkMode(HYBRID, 3).label == "hybrid, 3 days in office"
    assert WorkMode(HYBRID, 1).label == "hybrid, 1 day in office"
    assert WorkMode(HYBRID).label == "hybrid"
    assert WorkMode(ONSITE).label == "on-site"
