"""Experience-level extraction.

Works out three things about a posting so searchers can be filtered to roles they are
actually eligible for:

* ``min_years`` - the least experience the advert asks for
* ``is_internship`` - an internship, co-op or student placement
* ``is_graduate`` - a graduate programme, new-grad or early-career role

Two realities from the live data shaped this:

**Only about 45% of postings state a number at all.** So an unstated requirement must
never mean "exclude" — the filter's job is to drop roles that explicitly demand *more*
than the searcher has, not to drop everything that fails to mention a figure. Where no
number is given, the title's seniority is used as a much weaker fallback signal.

**Adverts contain years that are not requirements** — company history, visa rules,
project durations. So only figures anchored to the word "experience" (or an explicit
"minimum N years") are trusted, and among several the *lowest* is taken, because job
ads routinely list a low general bar alongside higher figures for specific niches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Anything above this is almost certainly not a requirement (company history, etc).
MAX_PLAUSIBLE_YEARS = 25

_QUALIFIER = (
    r"(?:relevant\s+|professional\s+|industry\s+|hands[- ]on\s+|practical\s+|"
    r"proven\s+|demonstrable\s+|work\s+|commercial\s+)?"
)

# Ranges are matched first so "2-3+ years of experience" yields the lower bound of 2
# rather than the 3 a simpler pattern would find by starting mid-range.
_YEAR_PATTERNS = [
    # "2-3 years of experience", "2 to 4 years' experience"
    re.compile(
        rf"(\d{{1,2}})\s*(?:-|–|—|to)\s*\d{{1,2}}\s*\+?\s*years?['’]?\s*"
        rf"(?:of\s+)?{_QUALIFIER}experience",
        re.I,
    ),
    # "3+ years of experience", "5 years' experience"
    re.compile(
        rf"(\d{{1,2}})\s*(?:\+|plus)?\s*years?['’]?\s*(?:of\s+)?{_QUALIFIER}experience",
        re.I,
    ),
    # "minimum of 3 years", "at least 5 years"
    re.compile(r"(?:minimum|at least|min\.?|no less than)\s+(?:of\s+)?(\d{1,2})\s*years?", re.I),
    # "experience: 3+ years"
    re.compile(r"experience[^.\n]{0,20}?(\d{1,2})\s*\+?\s*years?", re.I),
]

_INTERNSHIP = re.compile(
    r"\b(intern|internship|co[- ]?op|placement student|student placement|"
    r"summer analyst|work placement)\b",
    re.I,
)

# Matched against the title only. Bare "graduate" is enough there - "Graduate Software
# Engineer", "... Graduate Opportunities" - except where it names the recruiting job
# rather than the hire ("Graduate Recruiter"). "Trainee" covers the Irish accountancy
# route ("Trainee Accountant"), which is a graduate intake in all but name.
_GRADUATE = re.compile(
    r"\b(graduates?(?!\s+(?:recruit|talent|admission))|new grad|early careers?|"
    r"apprentice|apprenticeship|trainee|entry[- ]level|"
    r"fresher|campus hire|university hire)\b",
    re.I,
)

# A title's seniority implies a rough floor. Intentionally conservative: the aim is to
# stop a one-year candidate being shown Principal roles, not to make fine distinctions.
#
# Bare "manager" is deliberately absent. In "Project Manager", "Product Manager",
# "Account Manager" and "Customer Success Manager" it names a job function, not a
# seniority - treating it as senior wrongly inferred six years for entry-level roles.
# Only the compound forms that unambiguously mean people-leadership are listed.
_SENIORITY_YEARS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\b(intern|internship|placement)\b", re.I), 0),
    (re.compile(r"\b(graduate|new grad|junior|jnr|entry[- ]level|trainee|apprentice)\b", re.I), 0),
    (re.compile(r"\b(vp|vice president|head of|chief|director)\b", re.I), 10),
    (re.compile(r"\b(principal|distinguished|staff engineer|staff product)\b", re.I), 8),
    (re.compile(r"\b(team lead|tech lead|technical lead|engineering manager)\b", re.I), 6),
    (re.compile(r"\b(senior|snr|sr\.?|lead)\b", re.I), 5),
]


@dataclass(frozen=True)
class ExperienceProfile:
    min_years: int | None = None
    is_internship: bool = False
    is_graduate: bool = False
    # True when min_years came from the title rather than a stated requirement.
    inferred: bool = False


def extract_min_years(text: str | None) -> int | None:
    """Lowest stated experience requirement, or None if the advert states none."""
    if not text:
        return None

    found: list[int] = []
    for pattern in _YEAR_PATTERNS:
        for match in pattern.finditer(text):
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= MAX_PLAUSIBLE_YEARS:
                found.append(value)

    return min(found) if found else None


def infer_years_from_title(title: str) -> int | None:
    for pattern, years in _SENIORITY_YEARS:
        if pattern.search(title):
            return years
    return None


def analyze(title: str, description: str | None = None) -> ExperienceProfile:
    """Classify one posting's experience level."""
    title = title or ""
    haystack = f"{title}\n{description or ''}"

    is_internship = bool(_INTERNSHIP.search(title)) or bool(
        _INTERNSHIP.search(description or "") and _INTERNSHIP.search(title)
    )
    # An internship is nearly always signalled in the title; matching on description
    # alone turns every advert that mentions its intern programme into an internship.
    is_graduate = bool(_GRADUATE.search(title))

    if is_internship or is_graduate:
        return ExperienceProfile(
            min_years=0, is_internship=is_internship, is_graduate=is_graduate
        )

    stated = extract_min_years(haystack)
    inferred = infer_years_from_title(title)

    if stated is not None:
        # The title acts as a floor on the stated figure. A "Staff Engineer" advert
        # listing "7+ years overall" alongside a "2+ years with Kubernetes" bullet
        # would otherwise be read as a two-year role, putting it in front of people
        # who cannot get it.
        if inferred is not None and inferred > stated:
            return ExperienceProfile(min_years=inferred)
        return ExperienceProfile(min_years=stated)

    if inferred is not None:
        return ExperienceProfile(min_years=inferred, inferred=True)

    return ExperienceProfile()


def matches_experience(
    *,
    job_min_years: int | None,
    job_is_internship: bool,
    job_is_graduate: bool,
    candidate_years: int | None,
    want_internships: bool = False,
    want_graduate: bool = False,
) -> bool:
    """Is this posting appropriate for a searcher with `candidate_years` experience?

    When the searcher states nothing, everything is eligible. When they do state a
    figure, a posting is excluded only if it explicitly asks for *more* — an unstated
    requirement is never treated as disqualifying, since most adverts state none.

    Early-career roles are surfaced for searchers at or below two years and hidden
    above it, where they would be a waste of the reader's attention.

    The internship and graduate switches narrow to exactly those roles; ticked together
    they mean either. Experience is ignored under both, since each is an entry point by
    definition.
    """
    if want_internships or want_graduate:
        return (want_internships and job_is_internship) or (
            want_graduate and job_is_graduate
        )

    # Internships are never mixed into a general search; they are opted into.
    if job_is_internship:
        return False

    if candidate_years is None:
        return True

    if job_is_graduate:
        return candidate_years <= 2

    if job_min_years is None:
        return True

    return job_min_years <= candidate_years
