"""Which listings are careers events, and what kind each one is.

The sites these come from list everything: a search for "jobs fair" on Eventbrite also
returns a dozen near-identical paid networking evenings, and Meetup's Dublin tech page
sits beside speed dating. So an event is kept only on positive evidence: the listing's
own Career category, a careers word in its title, or, from a tech listing, a tech word.
A blocked word (dating, crochet, a book club) rules an event out whatever else it says.
"""

from __future__ import annotations

import re

# The filter chips on /events, in the order they appear.
KINDS = {
    "fair": "Job fairs",
    "student": "Graduate and student",
    "inclusive": "Inclusive hiring",
    "tech": "Tech meetups",
    "skills": "Workshops and talks",
    "networking": "Networking",
}

_BLOCK = re.compile(
    r"\b(?:dating|singles|speed ?dat\w*|date night|crochet|knit\w*|book (?:club|chat)|"
    r"read in company|yoga|run(?:ning)? club|runners|pub quiz|comedy|concert|gig|wine|"
    r"tasting|halloween|party|psychic|meditation|church|choir|toastmasters|"
    r"public speaking|language exchange|bridal|wedding|baby|parenting|fitness|"
    r"bingo|trivia|film club|walking tour|literary|date rounds?|quick dates?|folk club|"
    r"live at|ceremony|employment law|hair|colour masterclass)\b",
    re.IGNORECASE,
)

_CAREER = re.compile(
    r"\b(?:jobs?|careers?|recruitment|recruiting|hiring|employment|employability|"
    r"graduates?|grad|internships?|apprenticeships?|job ?seekers?|cv|r[ée]sum[ée]|"
    r"interview skills|work and skills|vacanc(?:y|ies)|early careers)\b",
    re.IGNORECASE,
)

_TECH = re.compile(
    r"\b(?:tech|technology|developers?|devs|engineering|engineers?|software|data|ai|"
    r"machine learning|ml|llms?|genai|cloud|aws|azure|gcp|devops|sre|srecon|kubernetes|"
    r"k8s|python|javascript|typescript|java|react|rust|golang|security|cyber\w*|"
    r"infosec|dub\|sec|hack(?:athon|ers?)?|ux|product|startups?|founders?|fintech|"
    r"blockchain|qa|testing|agile|open source|user group|ug|coding|programming|"
    r"analytics|ixda|design meetup)\b",
    re.IGNORECASE,
)

_INCLUSIVE = re.compile(
    r"\b(?:disabilit(?:y|ies)|disabled|autis\w*|neurodiver\w*|asiam|accessible employment|"
    r"refugees?|returners?)\b",
    re.IGNORECASE,
)
_STUDENT = re.compile(
    r"\b(?:graduates?|grad|students?|campus|undergraduates?|internships?|placements?|"
    r"early careers|roadshow|gradireland|university|college|atu|tu dublin|ucd|dcu|ucc|"
    r"tcd|nuig?|mtu|setu|dkit|ul|institute of technology)\b",
    re.IGNORECASE,
)
_FAIR = re.compile(
    r"\b(?:fairs?|expo|recruitment|recruiting|hiring|jobs? day|employment event|"
    r"work and skills|vacancies|open day)\b",
    re.IGNORECASE,
)
_SKILLS = re.compile(
    r"\b(?:cv|r[ée]sum[ée]|interview|workshop|webinar|talk|clinic|masterclass|"
    r"information session|info session|coaching|linkedin|bootcamp|training|seminar|"
    r"fireside|panel)\b",
    re.IGNORECASE,
)
_NETWORKING = re.compile(r"\bnetworking\b", re.IGNORECASE)


def is_blocked(title: str) -> bool:
    """Ruled out by its title alone, whatever else the listing says."""
    return bool(_BLOCK.search(title))


def is_careers_event(title: str, summary: str = "", *, career_tag: bool = False,
                     tech_hint: bool = False) -> bool:
    """Keep a listing only on positive evidence that it is about work."""
    if _BLOCK.search(title):
        return False
    if career_tag or _CAREER.search(title):
        return True
    return tech_hint and bool(_TECH.search(f"{title} {summary}"))


def kind_of(title: str, summary: str = "", *, source: str = "", tech_hint: bool = False) -> str:
    """The chip an event files under. The title decides first; the summary only helps."""
    text = f"{title} {summary}"
    if _INCLUSIVE.search(text):
        return "inclusive"
    if source == "gradireland" or _STUDENT.search(title):
        return "student"
    if _FAIR.search(title):
        return "fair"
    if _SKILLS.search(title):
        return "skills"
    if _NETWORKING.search(title):
        return "networking"
    if tech_hint or _TECH.search(title):
        return "tech"
    if _FAIR.search(summary):
        return "fair"
    return "skills"
