"""Facts an advert states in its prose, read out so a searcher sees them before opening it.

Each reader here answers from the advert's own words or not at all. That is the product's
rule for every figure it shows (PRODUCT.md, "absence of evidence is not evidence of
absence"): an advert that does not say how often the office expects you is shown as
"not stated", never guessed from the employer or the title. So each pattern is written
for precision first. Missing a stated fact costs a chip; inventing one sends someone to
an interview under a false impression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------- work mode

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

# "Hybrid" is also a technical word (hybrid cloud, hybrid infrastructure, a hybrid app),
# so only the working-pattern senses count: a LinkedIn tag, or "hybrid" next to a word
# about how or where the work is done.
_HYBRID = re.compile(
    r"#li-hybrid\b"
    r"|\bhybrid[ -](?:working|work|role|model|basis|schedule|arrangement|policy|workplace|"
    r"position|opportunit\w*|setup|set-up|pattern|approach|environment for|office)\b"
    r"|\b(?:is|a|our|on a)\s+hybrid\b(?!\s+(?:cloud|infra\w*|app\w*|mobile|search|systems?|of|go\b|between|mix))"
    r"|\blocation:\s*hybrid\b|\bhybrid\s*(?:/|or)\s*(?:remote|flexible)\b"
    r"|\bhybrid\s*\(|\bhybrid (?:from|in) (?:our )?(?:dublin|cork|ireland)",
    re.IGNORECASE,
)
_REMOTE = re.compile(
    r"#li-remote\b|\b(?:fully|100%|completely) remote\b|\bremote[- ]first\b"
    r"|\bremote (?:role|position|opportunity|job)\b|\bwork from anywhere\b"
    r"|\bremote (?:within|in|across) (?:ireland|the uk|europe|emea|the eu)\b",
    re.IGNORECASE,
)
_ONSITE = re.compile(
    r"#li-onsite\b|\b(?:fully|100%|completely) (?:on-?site|in[- ]office|office[- ]based)\b"
    r"|\b(?:on-?site|office[- ]based|in[- ]office) (?:role|position)\b"
    r"|\bthis role is (?:based )?(?:fully )?on-?site\b",
    re.IGNORECASE,
)
# "3 days per week in the office", "two days per week in our Dublin office",
# "three days a week onsite".
_OFFICE_DAYS = re.compile(
    r"\b([1-5]|one|two|three|four|five)\s*(?:\([1-5]\)\s*)?days?\s*(?:a|per|each)?\s*week"
    r"\s*(?:in|at|from|on)?\s*(?:the|our)?\s*(?:[A-Z][a-z]+\s)?(?:office|site|onsite|on-site)\b",
    re.IGNORECASE,
)

REMOTE = "remote"
HYBRID = "hybrid"
ONSITE = "on-site"


@dataclass(frozen=True)
class WorkMode:
    kind: str                    # REMOTE, HYBRID or ONSITE
    office_days: int | None = None

    @property
    def label(self) -> str:
        """As a chip says it: "hybrid", "hybrid, 3 days in office", "on-site"."""
        if self.kind == HYBRID and self.office_days:
            return f"hybrid, {self.office_days} day{'s' if self.office_days > 1 else ''} in office"
        return self.kind


def work_mode(description: str | None, *, is_remote: bool = False) -> WorkMode | None:
    """How the advert says the work is done, or None when it does not say.

    Hybrid wins over remote when an advert says both ("hybrid or remote within the
    UK"): the office is on the table, and someone who needs remote should read the
    advert. A stated number of office days settles it either way, five meaning on-site.
    """
    text = " ".join((description or "").split())
    days = None
    match = _OFFICE_DAYS.search(text)
    if match:
        word = match.group(1).lower()
        days = int(word) if word.isdigit() else _NUM.get(word)
    if days == 5:
        return WorkMode(ONSITE)
    if days or _HYBRID.search(text):
        return WorkMode(HYBRID, days)
    if _REMOTE.search(text) or is_remote:
        return WorkMode(REMOTE)
    if _ONSITE.search(text):
        return WorkMode(ONSITE)
    return None


# ------------------------------------------------------------------------ salary

# One amount in euro: "€55,000", "€76.000" (a European thousands point), "€71,440.00",
# "€55k", "EUR 55,000", "55,000 EUR". Pounds and dollars are not read: every job here is
# in Ireland, and a sterling figure in a Dublin advert is for somewhere else.
_AMOUNT = (
    r"(?:€|EUR)\s?(?P<{n}>\d{{1,3}}(?:[.,  ]\d{{3}})+(?:[.,]\d{{2}})?|\d+(?:[.,]\d+)?)\s?(?P<{n}k>[kK]\b)?"
)
_BARE = r"(?P<{n}>\d{{1,3}}(?:[.,  ]\d{{3}})+(?:[.,]\d{{2}})?|\d+(?:[.,]\d+)?)\s?(?P<{n}k>[kK]\b)?\s?(?:EUR\b)?"
_RANGE = re.compile(
    _AMOUNT.format(n="lo") + r"(?:\s?EUR\b)?"
    + r"(?:\s*(?:-|–|—|to|and)\s*(?:" + _AMOUNT.format(n="hi") + r"(?:\s?EUR\b)?|"
    + _BARE.format(n="hi2") + r"))?",
)
_AFTER_EUR = re.compile(
    r"\b(?P<lo>\d{1,3}(?:[.,]\d{3})+)\s*(?:-|–|—|to)\s*(?P<hi>\d{1,3}(?:[.,]\d{3})+)\s*EUR\b"
)
# The sign after the figure, French style: "93 280,00 € - 139 920,00 €".
_SIGN_AFTER = re.compile(
    r"(?P<lo>\d{1,3}(?:[.\u202f\u00a0 ]\d{3})+(?:,\d{2})?)\s?\u20ac"
    r"(?:\s*(?:-|\u2013|\u2014|to)\s*(?P<hi>\d{1,3}(?:[.\u202f\u00a0 ]\d{3})+(?:,\d{2})?)\s?\u20ac)?"
)
# Words that make a nearby figure a salary, and ones that make it anything but.
_PAY_WORDS = re.compile(
    r"salary|pay\b|pay range|compensation|remuneration|\bOTE\b|per annum|p\.a\.|annual|"
    r"base\b|package|daily rate|hourly rate|day rate|wage|earn",
    re.IGNORECASE,
)
_NOT_PAY = re.compile(
    r"^\s*(?:/\s*£?\d+\s*)?(?:m\b|bn\b|million|billion|revenue|turnover|in funding|funding|raised|"
    r"investment|assets|of revenue)",
    re.IGNORECASE,
)
_ELSEWHERE = re.compile(
    r"^[\s,.;:)(-]*(?:EUR\s*)?(?:germany|france|spain|netherlands|poland|portugal|italy|"
    r"belgium|austria|switzerland|sweden|denmark|finland|norway|czech|romania|"
    r"united kingdom|\buk\b|london|berlin|munich|amsterdam|paris|madrid|barcelona|lisbon|"
    r"warsaw|prague|united states|\bus\b|usa|canada|singapore|india)",
    re.IGNORECASE,
)

YEAR, DAY, HOUR = "year", "day", "hour"
_PLAUSIBLE = {YEAR: (15_000, 600_000), DAY: (80, 3_000), HOUR: (10, 300)}


@dataclass(frozen=True)
class Salary:
    low: int
    high: int
    period: str = YEAR
    ote: bool = False

    def _money(self, value: int, short: bool) -> str:
        if short and self.period == YEAR and value >= 1000:
            return f"€{value / 1000:.0f}k" if value % 1000 == 0 or value >= 100_000 else f"€{value / 1000:.1f}k"
        return f"€{value:,}"

    def text(self, short: bool = False) -> str:
        """"€55,000 to €75,000 a year", or "€55k to €75k" for a chip."""
        amount = self._money(self.low, short)
        if self.high != self.low:
            amount += " to " + self._money(self.high, short)
        if not short or self.period != YEAR:
            amount += " an hour" if self.period == HOUR else f" a {self.period}"
        return amount + (" OTE" if self.ote else "")

    @property
    def chip(self) -> str:
        return self.text(short=True)

    @property
    def yearly_high(self) -> int:
        """The top of the range as a full-time year, for the filter bar's Salary picker.

        An hour is counted as 1,950 a year (37.5 a week) and a day as 230 (a working
        year less annual leave and bank holidays), so a day rate and an hourly wage sit
        on the same scale as an annual salary. Only the comparison uses this; the advert's
        own figure is what any page shows.
        """
        return round(self.high * {YEAR: 1, DAY: 230, HOUR: 1950}[self.period])


def _euros(number: str, thousands: str | None) -> float:
    if re.fullmatch(r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{2})?", number):
        if re.search(r"[.,]\d{2}$", number) and not re.search(r"[.,]\d{3}$", number):
            number = number[:-3]  # drop the cents: "71,440.00"
        value = float(re.sub(r"[.,  ]", "", number))
    else:
        value = float(number.replace(",", "."))
    return value * 1000 if thousands else value


def salary(description: str | None) -> Salary | None:
    """The pay the advert states for this job in Ireland, or None when it states none.

    A figure only counts with a pay word near it (salary, pay range, OTE, per annum...),
    so revenue, funding and fees quoted in an employer's blurb are never read as pay. A
    range labelled for another country is passed over for the one labelled Ireland, or
    for nothing.
    """
    text = " ".join((description or "").split())
    if "€" not in text and "EUR" not in text:
        return None
    candidates = [(m.start(), m.end(), m.group("lo"), m.group("lok"), m.group("hi") or m.group("hi2"),
                   m.group("hik") or m.group("hi2k")) for m in _RANGE.finditer(text)]
    candidates += [(m.start(), m.end(), m.group("lo"), None, m.group("hi"), None)
                   for pattern in (_AFTER_EUR, _SIGN_AFTER) for m in pattern.finditer(text)]
    for start, end, lo, lok, hi, hik in sorted(candidates):
        after = text[end:end + 60]
        before = text[max(0, start - 120):start]
        if _NOT_PAY.match(after) or _ELSEWHERE.match(after):
            continue
        if not (_PAY_WORDS.search(before) or re.match(r"\s*(?:OTE|per|a year|an hour|a day|p\.a\.)", after, re.I)):
            continue
        low = _euros(lo, lok)
        high = _euros(hi, hik or lok) if hi else low
        if high < low:
            low, high = high, low
        window = (after[:40]).lower()
        period = HOUR if re.search(r"\b(?:per|an|a|/)\s?hour|hourly", window + before[-30:].lower()) else (
            DAY if re.search(r"\b(?:per|a|/)\s?day\b|daily|day rate", window + before[-30:].lower()) else YEAR)
        floor, ceiling = _PLAUSIBLE[period]
        if not (floor <= low <= ceiling and floor <= high <= ceiling) or high > low * 4:
            continue
        ote = bool(re.search(r"\bOTE\b|on[- ]target", before[-80:] + after[:30], re.IGNORECASE))
        return Salary(round(low), round(high), period, ote)
    return None
