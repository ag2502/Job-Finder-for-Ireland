"""When a part-time job's hours fall. Every case is taken from a live Irish advert."""

from __future__ import annotations

import pytest

from jobfinder.normalize.shifts import WHEN_BITS, weekly_hours, when_bits, when_mentioned


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Flexibility to work evenings, weekends, and bank holidays", {"evenings", "weekends"}),
        ("Must be able to work flexible hours Monday to Sunday incl. weekends and evenings.",
         {"evenings", "weekends"}),
        ("Weekend Rate: Saturday & Sunday Rate Sign On Bonus", {"weekends"}),
        ("Premium Rate on Sunday Shifts (1.5 x Normal Rate)", {"weekends"}),
        ("Shift Patterns: Morning shift: Starting at 5am or 6am with your team", {"mornings"}),
        ("working shifts across Early Mornings, Afternoons, Evenings & Overnights",
         {"mornings", "evenings", "nights"}),
        ("Team Leader (Days OR Nights) Location Twisel Lodge", {"nights"}),
        ("Additional payments will be made for weekends, public holidays and night duty",
         {"weekends", "nights"}),
        ("Required Availability: Morning, lunchtime, evening and bed time calls",
         {"mornings", "evenings"}),
    ],
)
def test_reads_when_the_advert_says(text: str, expected: set[str]) -> None:
    assert when_mentioned("Care Assistant", text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "the organisation ranked 2nd in The Sunday Independent's Best Employer list",
        "operating 9 a.m. to 6 p.m. Monday to Saturday and closed on Sunday.",
        "Some flexibility needed. No Sunday work.",
        "national events (Carer of the Year, Respite weekends, Training)",
        "Premium pay for some overnight hours, extra reward for late-night hours.",
        "putting the customer first, first thing in the morning or last thing at night.",
        "Monday to Friday, no weekends, no evenings.",
    ],
)
def test_ignores_mentions_that_are_not_the_hours(text: str) -> None:
    assert when_mentioned("Sales Assistant", text) == set()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Night Porter", {"nights"}),
        ("Overnight Customer Assistant", {"nights"}),
        ("Food & Beverage Assistant - Breakfast Mid Week", {"mornings"}),
        ("Christmas Sales Assistant, Omni", {"seasonal"}),
        ("8hr Stylist (seasonal)", {"seasonal"}),
        ("Seasonal Sales Associate, Tommy Hilfiger Limerick", {"seasonal"}),
        # A childcare title, not a shift.
        ("Early Years Educator", set()),
        ("Deli Assistant (Part-Time)", set()),
    ],
)
def test_reads_the_title(title: str, expected: set[str]) -> None:
    assert when_mentioned(title, None) == expected


def test_reads_a_seasonal_contract_in_the_text() -> None:
    assert "seasonal" in when_mentioned("Sales Assistant", "This is a seasonal contract until January.")


def test_bits_round_trip() -> None:
    assert when_bits({"weekends", "evenings"}) == WHEN_BITS["weekends"] | WHEN_BITS["evenings"]
    assert when_bits(set()) == 0


@pytest.mark.parametrize(
    ("title", "text", "expected"),
    [
        ("Retail Assistant (Temp Athlete) - PT8HRS Kildare", None, "8 hrs a week"),
        ("8hr Sales Stylist (SEASONAL TEMP)", None, "8 hrs a week"),
        ("Brown Thomas Dundrum, Beauty Advisor, 10 hours", None, "10 hrs a week"),
        ("Healthcare Assistant", "The position on offer is 10 hours a week, 12 months fixed term",
         "10 hrs a week"),
        ("Home Support", "Band A need to be available two evenings - 11.5 hrs-14 hrs per week",
         "11.5 to 14 hrs a week"),
        ("Customer Service Advisor 25 hours per week", None, "25 hrs a week"),
    ],
)
def test_weekly_hours(title: str, text: str | None, expected: str) -> None:
    assert weekly_hours(title, text) == expected


@pytest.mark.parametrize(
    ("title", "text"),
    [
        # The pay basis of a full-time post, not this role's hours.
        ("Staff Nurse", "Salary quoted is based on a 39 hour week."),
        ("Nurse", "Working Pattern: 39 hours per week"),
        ("Care Assistant", "Must be able to do 12 Hour Shifts"),
        ("Deli Assistant", None),
    ],
)
def test_no_weekly_hours(title: str, text: str | None) -> None:
    assert weekly_hours(title, text) is None
