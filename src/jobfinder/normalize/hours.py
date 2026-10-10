"""Working-hours classification: is a posting part-time?

Three signals, any one of which is enough:

* the board's own employment type, where it gives one (Workday's ``timeType``,
  SmartRecruiters' ``typeOfEmployment``, schema.org ``employmentType``, ...)
* the title, which is where most Irish retail and hospitality employers say it:
  "Part Time Retail Sales Consultant - Bray", "Room Attendant (Part Time)"
* the advert, but only where it states the role's own hours

That last qualifier is the whole difficulty. Across ~8,400 live Irish adverts, "part
time" appears in about 160 descriptions, and a good share of them are not part-time
roles at all: "support for part-time training", "graduates on the part time course",
"another part-time Technician role" describing a colleague, and the university pay
clause "pro rata for shorter and/or part-time contracts" on full-time research posts.
So a mention in the text counts only when it is a labelled field ("Contract Type: Perm,
part-time"), a statement about this role ("This is a part-time, permanent
opportunity", "on a part time basis"), or an offer of both ("full and part time
contracts available"). Weekly hours under thirty and an FTE fraction are counted too,
since those are how the public sector and the forecourt chains write it ("Work on a
15-hour weekly contract", "0.5 FTE").

An advert offering full or part time is part-time for this purpose: a searcher who
ticks "Part-time only" can apply for it on part-time hours.
"""

from __future__ import annotations

import re

_PART_TIME = r"part[\s\-‐]?time"

# The board's own label, normalised by dropping everything but letters:
# "PART_TIME", "Part-time", "parttime", "PartTime", "Part time, Permanent".
_TYPE_PART = re.compile(r"parttime|halftime|casual|hourly|sessional|zerohours?")

_TITLE = re.compile(
    rf"\b{_PART_TIME}\b|\bp/t\b|\bhalf[\s-]?time\b|\bzero[\s-]hours?\b|\bsessional\b"
    r"|\bhourly[\s-]paid\b|\bcasual\b"
    # A "Weekend Shift" in a plant is a compressed full-time week; weekend *staff* are not.
    r"|\bweekends?\s+(?:only|staff|team|assistant|associate|position|role|job|work|crew)\b",
    re.I,
)

# What follows "part time" when it is not about the role's own hours.
_NOT_THE_ROLE_AFTER = re.compile(
    r"\s*(?:training|study|studies|studying|course|courses|degree|degrees|mba|masters?|"
    r"master's|phd|doctorate|education|programmes?|programs?|scholarships?|funded|"
    r"support|students?|learners?|postgraduate|undergraduate|qualification|basis\s+study)\b",
    re.I,
)
# What precedes it when the sentence is about someone or something else - a colleague,
# the pay clause, or the candidate's past ("Experience gained through an internship,
# part-time role, volunteering", which every graduate programme advert carries).
_NOT_THE_ROLE_BEFORE = re.compile(
    r"\b(?:pro[\s-]?rata|another|other|existing|our|shorter and/or|shorter or)\b[^.\n]{0,25}$"
    r"|internships?\s*(?:,|or|/|and)\s*$"
    r"|experience\s+(?:gained|in|as|through|from|of)\b[^.\n]{0,45}$",
    re.I,
)
_PAST_WORK_AFTER = re.compile(r"\s*(?:role|work|job)s?\s*(?:,|or|and)\s*(?:volunteer|universit|extra)", re.I)

_LABELLED = re.compile(
    r"\b(?:employment|job|contract|position|role|post|work(?:ing)?|time|hours?|schedule|"
    r"tenure|vacancy)\s*(?:type|pattern|basis|status|of work|/\s*duration)?\s*[:\-–]\s*"
    rf"[^.\n:]{{0,30}}?{_PART_TIME}",
    re.I,
)
_STATED = re.compile(
    rf"(?:\b(?:is|for|as)\s+an?|\btemporary|\bpermanent|\bfixed[\s-]term|\bon\s+a)\s*,?\s*"
    rf"{_PART_TIME}|{_PART_TIME}\s*,?\s*(?:\(|role|position|post|contract|opportunity|"
    r"job|vacancy|hours|basis|work|shifts?|permanent|temporary|fixed|available|"
    r"roles|positions|posts|contracts|staff|team|assistant|cleaner|worker)",
    re.I,
)
_BOTH = re.compile(
    rf"full[\s\-‐]?time\s*(?:and|or|/|&|,)\s*{_PART_TIME}|{_PART_TIME}\s*(?:and|or|/|&|,)\s*"
    r"full[\s\-‐]?time|\bfull\s*(?:and|or|/|&)\s*part[\s\-‐]?time",
    re.I,
)

# "20 hours per week", "16-24 hrs a week", "15 hrs/wk", "a 15-hour weekly contract".
_WEEKLY_HOURS = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s*(?:(?:-|–|to)\s*(\d{1,2}(?:\.\d{1,2})?)\s*)?"
    r"(?:-\s*)?(?:hours?|hrs?)\s*(?:(?:per|a|each|every)\s+week|/\s*(?:week|wk)|"
    r"weekly|p\.?/?w\b|pw\b)",
    re.I,
)
# A title states the weekly contract bare: "Retail Sales Advisor - 20 Hours", "8hr
# tailor", Nike's "PT 20H". Plural or abbreviated only, and not before "shift", since
# "12 Hour Shifts" and "24 Hour Gym" describe something else.
_TITLE_HOURS = re.compile(
    r"(?i:(?<![\d.])(\d{1,2}(?:\.\d)?)\s*(?:-\s*)?(?:hours|hrs|hr)\b(?!\s*shifts?))"
    r"|\bPT\s?(\d{1,2})\s?H(?:RS?)?\b",
)
_FTE = re.compile(r"(?<![\d.])0?\.\d{1,2}\s*(?:fte|wte)\b|\bhalf[\s-]time\b", re.I)

# A full-time week in Ireland is 35 to 40 hours. Thirty is the usual line drawn by
# employers' own "part time (up to 30 hours)" wording.
PART_TIME_HOURS = 30


def _type_says_part_time(employment_type: str | None) -> bool:
    if not employment_type:
        return False
    letters = re.sub(r"[^a-z]", "", employment_type.lower())
    return bool(_TYPE_PART.search(letters))


def _hours_say_part_time(text: str) -> bool:
    for match in _WEEKLY_HOURS.finditer(text):
        figures = [float(value) for value in match.groups() if value]
        if figures and all(0 < figure < PART_TIME_HOURS for figure in figures):
            return True
    return False


def _mentions_role_hours(text: str) -> bool:
    for match in re.finditer(_PART_TIME, text, re.I):
        start, end = match.span()
        if _NOT_THE_ROLE_AFTER.match(text, end) or _PAST_WORK_AFTER.match(text, end):
            continue
        if _NOT_THE_ROLE_BEFORE.search(text[max(0, start - 40) : start]):
            continue
        window = text[max(0, start - 60) : end + 40]
        if _LABELLED.search(window) or _STATED.search(window) or _BOTH.search(window):
            return True
    return False


def is_part_time(
    title: str | None,
    description: str | None = None,
    employment_type: str | None = None,
) -> bool:
    """Whether a posting is part-time, or offers part-time hours."""
    title = title or ""
    if _type_says_part_time(employment_type):
        return True
    if _TITLE.search(title) or _FTE.search(title) or _hours_say_part_time(title):
        return True
    for match in _TITLE_HOURS.finditer(title):
        figure = float(match.group(1) or match.group(2))
        if 0 < figure < PART_TIME_HOURS:
            return True
    text = description or ""
    if not text:
        return False
    # The opening of an advert carries its terms. Further down, a weekly figure is as
    # likely to be about something else ("up to 20 hours a week of study leave"), so the
    # hours and FTE tests read only the first stretch.
    return (
        _mentions_role_hours(text)
        or bool(_FTE.search(text[:1500]))
        or _hours_say_part_time(text[:1500])
    )
