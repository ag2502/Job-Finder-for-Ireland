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


# ------------------------------------------------------------------------ salary

from jobfinder.normalize.facts import DAY, HOUR, Salary, salary  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("Salary Range: €55,000-€75,000 Share in our success", Salary(55_000, 75_000)),
    ("Base Pay Range in Local Currency: €76.000 — €114.000 EUR", Salary(76_000, 114_000)),
    ("Full Time Salary Range: €71,440.00 - €107,160.00 ----", Salary(71_440, 107_160)),
    ("Primary Location Base Pay Range: €70,200 EUR - €105,400 EUR Ireland", Salary(70_200, 105_400)),
    ("pay (base salary and on target incentive pay) for this role is €70,000 OTE per year.",
     Salary(70_000, 70_000, ote=True)),
    ("the estimated base salary range is between €77,000 - €98,000 .", Salary(77_000, 98_000)),
    ("Salary: €55k to €70k plus pension", Salary(55_000, 70_000)),
    ("Hourly rate: €25 per hour", Salary(25, 25, HOUR)),
    ("Contract day rate €450 - €550 per day", Salary(450, 550, DAY)),
    ("Salary range 55,000 - 65,000 EUR depending on experience", Salary(55_000, 65_000)),
    ("Full Time Salary Range: 93 280,00 \u20ac - 139 920,00 \u20ac ----", Salary(93_280, 139_920)),
])
def test_the_salary_the_advert_states(text, expected):
    assert salary(text) == expected


@pytest.mark.parametrize("text", [
    "3300+ strong, €350/£300m revenue business",
    "revenues exceeding €347m/£302m, Version 1 is a market leader",
    "We raised €20 million in funding last year.",
    "Pay Range: €35,200 EUR - €52,800 EUR Germany",
    "Salary: £55,000 - £65,000",
    "Manage a budget of €2,000,000 across the region.",
    "No figures here at all.",
    None,
])
def test_no_salary_stated(text):
    assert salary(text) is None


def test_the_irish_range_wins_over_another_countrys():
    text = ("Pay Range: €35,200 EUR - €52,800 EUR Germany. "
            "Primary Location Base Pay Range: €70,200 EUR - €105,400 EUR Ireland")
    assert salary(text) == Salary(70_200, 105_400)


def test_how_a_salary_reads():
    assert Salary(55_000, 75_000).text() == "€55,000 to €75,000 a year"
    assert Salary(55_000, 75_000).chip == "€55k to €75k"
    assert Salary(71_440, 107_160).chip == "€71.4k to €107k"
    assert Salary(70_000, 70_000, ote=True).chip == "€70k OTE"
    assert Salary(25, 25, HOUR).chip == "€25 an hour"
