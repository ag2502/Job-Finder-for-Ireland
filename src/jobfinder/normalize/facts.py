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
