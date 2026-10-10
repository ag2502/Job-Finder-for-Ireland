"""When a part-time job's hours fall, as the advert states them.

The part-time page lets a searcher say when they are free (mornings, evenings, nights,
weekends) and keeps the adverts that mention working then. Two things make that worth
doing carefully rather than with a bare word search:

* An advert mentions weekends for reasons that are not the job: "ranked by The Sunday
  Independent", "closed on Sunday", "No Sunday work", "Respite weekends" (a service the
  charity runs). Those are dropped, and the rest of the advert is still read.
* Chains repeat boilerplate in every advert. Circle K's "Premium pay for some overnight
  hours" sits under day roles too, and "first thing in the morning or last thing at
  night" is a description of service, not of shifts. Nights therefore need a working
  word ("night shift", "nights", "sleepover", "night duty"), never a lone "night".

What is read is what the advert says, nothing inferred: an advert that never says when
the hours are is simply not in any of these, and the page says how many of those there
are rather than guessing.

Weekly hours are read the same way `normalize.hours` reads them, and shown only when
the figure is a part-time one, so the pay basis "based on a 39 hour week" is not shown
as the role's own hours.
"""

from __future__ import annotations

import re

from jobfinder.normalize.hours import PART_TIME_HOURS

# The order the page lists them in, and the bit each sets in the page's data.
WHEN = ("mornings", "evenings", "nights", "weekends", "seasonal")
WHEN_BITS = {name: 1 << i for i, name in enumerate(WHEN)}
WHEN_LABELS = {
    "mornings": "Mornings",
    "evenings": "Evenings",
    "nights": "Nights",
    "weekends": "Weekends",
    "seasonal": "Seasonal",
}

# How far into an advert to read. The terms come first; further down, a long advert
# drifts into the employer's history and its other services.
_READ = 4000

_WEEKENDS = re.compile(
    r"\bweekends?\b|\bsat(?:urday)?s?\s*(?:&|and|/|,|to|-|–)\s*sun(?:day)?s?\b"
    r"|\bmon(?:day)?\s*(?:to|-|–|through)\s*sun(?:day)?\b"
    r"|\b(?:saturdays?|sundays?)\s+(?:shifts?|work|working|mornings?|afternoons?|evenings?|"
    r"nights?|hours|rate|premium|cover|availability|and\s+bank)"
    r"|\b(?:every|alternate|alternative|some|one|occasional)\s+(?:saturday|sunday)s?\b"
    r"|\b(?:saturday|sunday)\s+only\b",
    re.I,
)
# What turns a mention into something other than the role's own hours.
_NOT_WEEKENDS = re.compile(
    r"\b(?:no|free|respite|long|bank\s+holiday)\s+weekends?\b|\bweekends?\s+off\b"
    r"|\bsunday\s+(?:independent|times|business\s+post|world|tribune)\b"
    r"|\bclosed\s+(?:on\s+)?(?:saturdays?|sundays?|weekends?)\b|\bno\s+(?:saturday|sunday)\b",
    re.I,
)

_EVENINGS = re.compile(
    r"\bevenings?\b(?!\s+(?:classes|class|courses?|events?|meals?|news|wear|standard))"
    r"|\blate\s+(?:shifts?|evenings?|finishes)\b|\bclosing\s+shifts?\b",
    re.I,
)
_NOT_EVENINGS = re.compile(r"\bno\s+(?:late\s+)?evenings?\b|\bevenings?\s+off\b|\bevenings?\s+free\b", re.I)

_NIGHTS = re.compile(
    r"\bnights\b(?!\s+(?:out|away|stay|accommodation|'))"
    r"|\bovernights\b|\bnight[\s-]*(?:shifts?|duty|duties|work|staff|cover|porter|"
    r"supervisor|manager|auditor|carer|nurse|worker|rota|time\s+hours)\b"
    r"|\bwaking\s+nights?\b|\bsleep[\s-]?overs?\b|\bsleep[\s-]?in\s+shifts?\b"
    r"|\bovernight\s+(?:shifts?|work|support|cover|staff|crew|customer|team|assistant|"
    r"duty|duties)\b",
    re.I,
)
_NOT_NIGHTS = re.compile(r"\bno\s+(?:late\s+)?nights?\b|\bno\s+night\s+(?:shifts?|work)\b", re.I)

_MORNINGS = re.compile(
    r"\bmornings\b|\bmorning\s+(?:shifts?|hours|work|calls|coaching|cover|rota|availability)\b"
    r"|\bearly\s+(?:shifts?|starts?|mornings?)\b|\bbreakfast\s+(?:shifts?|service|staff|team|"
    r"server|chef|cook|assistant|attendant)\b"
    r"|\bmorning\s*(?:,|/|&|and|or)\s*(?:lunchtime|afternoon|midday|evening|night)",
    re.I,
)
# Not a bare "early": "Early Years Educator" is a childcare title, not a shift.
_TITLE_MORNINGS = re.compile(r"\bmornings?\b|\bbreakfast\b|\bearly\s+shifts?\b", re.I)
_TITLE_EVENINGS = re.compile(r"\bevenings?\b|\blate\s+shifts?\b", re.I)
_TITLE_NIGHTS = re.compile(r"\bnights?\b|\bovernight\b|\bsleep[\s-]?overs?\b", re.I)

# Seasonal work is said in the title ("Christmas Sales Assistant", "Seasonal Team
# Member") or as the contract ("a seasonal contract until January").
# Every festival reads as "seasonal" on the page, so nothing there names one.
_SEASONAL_TITLE = re.compile(
    r"\bchristmas\b|\bxmas\b|\bseasonal\b|\bfestive\b|\beaster\b|\bhalloween\b|"
    r"\bblack\s+friday\b|\bsummer\s+(?:staff|team|camp|"
    r"season|job|work|assistant|associate|crew|position|role|student)",
    re.I,
)
_SEASONAL_TEXT = re.compile(
    r"\bseasonal\s+(?:role|contract|position|post|vacanc(?:y|ies)|team|staff|work|"
    r"opportunit(?:y|ies)|associates?|colleagues?|hires?|recruitment|temp)"
    r"|\b(?:christmas|festive|easter|halloween)\s+(?:period|season|temp|contract|role|position|team|staff|"
    r"temporary|peak|trading)"
    r"|\btemporary\s+(?:christmas|seasonal|summer)\b",
    re.I,
)

# As `normalize.hours` has it, and also "11.5 hrs-14 hrs per week", the unit said twice.
_WEEKLY_HOURS = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s*"
    r"(?:(?:(?:hours?|hrs?)\s*)?(?:-|–|to)\s*(\d{1,2}(?:\.\d{1,2})?)\s*)?"
    r"(?:-\s*)?(?:hours?|hrs?)\s*(?:(?:per|a|each|every)\s+week|/\s*(?:week|wk)|"
    r"weekly|p\.?/?w\b|pw\b)",
    re.I,
)
_TITLE_HOURS = re.compile(
    r"(?i:(?<![\d.])(\d{1,2}(?:\.\d)?)\s*(?:-\s*)?(?:hours|hrs|hr)\b(?!\s*shifts?))"
    r"|\bPT\s?(\d{1,2})\s?H(?:RS?)?\b",
)


def _says(text: str, wanted: re.Pattern[str], unwanted: re.Pattern[str]) -> bool:
    """Whether a mention survives once the ones about something else are struck out."""
    if not wanted.search(text):
        return False
    return bool(wanted.search(unwanted.sub(" ", text)))


def when_mentioned(title: str | None, description: str | None = None) -> set[str]:
    """The times of the week an advert says the work falls in."""
    title = title or ""
    text = f"{title}\n{(description or '')[:_READ]}"
    found: set[str] = set()
    if _TITLE_MORNINGS.search(title) or _MORNINGS.search(text):
        found.add("mornings")
    if _TITLE_EVENINGS.search(title) or _says(text, _EVENINGS, _NOT_EVENINGS):
        found.add("evenings")
    if _TITLE_NIGHTS.search(title) or _says(text, _NIGHTS, _NOT_NIGHTS):
        found.add("nights")
    if _says(text, _WEEKENDS, _NOT_WEEKENDS):
        found.add("weekends")
    if _SEASONAL_TITLE.search(title) or _SEASONAL_TEXT.search(text):
        found.add("seasonal")
    return found


def when_bits(found: set[str]) -> int:
    return sum(WHEN_BITS[name] for name in found)


def _figure(value: float) -> str:
    return f"{value:g}"


def weekly_hours(title: str | None, description: str | None = None) -> str | None:
    """The role's weekly hours as the advert gives them ("20 hrs a week", "16 to 24 hrs
    a week"), or None. Only a part-time figure counts: a range may run up to a full week,
    but it has to start below one."""
    title = title or ""
    for match in _TITLE_HOURS.finditer(title):
        figure = float(match.group(1) or match.group(2))
        if 0 < figure < PART_TIME_HOURS:
            return f"{_figure(figure)} hrs a week"
    for text in (title, (description or "")[:1500]):
        for match in _WEEKLY_HOURS.finditer(text):
            low, high = (float(v) if v else None for v in match.groups())
            if low is None or not 0 < low < PART_TIME_HOURS:
                continue
            if high is None or high == low:
                return f"{_figure(low)} hrs a week"
            if low < high < 40:
                return f"{_figure(low)} to {_figure(high)} hrs a week"
    return None
