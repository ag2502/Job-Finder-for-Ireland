"""How well a CV reads to an applicant tracking system, for one job.

No ATS publishes its scoring, so this does not pretend to be any one of them. It checks
what they all do: read the file's text, find the standard sections, pull out contact
details, and match the CV's words against the advert's requirements - which is what a
recruiter's keyword filter runs on. Every point awarded has a reason written next to it.

Deliberately deterministic: the same CV and the same job give the same score every
time, so a change in the score is a change in the CV, never noise from a model.
"""

from __future__ import annotations

import re

from jobfinder.normalize.taxonomy import extract_skills

# Skills whose name is also an everyday word, which the taxonomy finds in any advert
# ("go further", "R&D"). Only counted when the model named them as a requirement too.
_AMBIGUOUS = {"go", "r", "c", "rust", "swift", "excel", "access", "spring", "ruby", "chef"}

_SECTIONS = {
    "experience": ("experience", "employment", "work history", "career history",
                   "professional history", "positions held"),
    "education": ("education", "qualifications", "academic", "training"),
    "skills": ("skills", "technical skills", "competencies", "technologies", "tools",
               "expertise", "core skills"),
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{3,4}")
_LINK = re.compile(r"linkedin\.com|github\.com|https?://", re.I)
_NUMBER = re.compile(r"\d")
_SENIORITY = re.compile(
    r"\b(senior|sr|junior|jr|lead|principal|staff|head|chief|graduate|intern|associate|"
    r"entry[- ]level|mid[- ]level|i{1,3}|iv|[123])\b\.?", re.I,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("&", " and "))


_WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]")


def _stem(word: str) -> str:
    """A light stem, enough that "tests" finds "testing", "optimise" finds "optimized"
    and "modelling" finds "modeling" - the forgiveness real ATS keyword matching has.
    Short words and technical names ("c++", "node.js") are left alone."""
    w = word.lower().replace("iz", "is")
    if len(w) <= 3 or not w.isalpha():
        return w
    for suffix, repl in (("isation", "is"), ("ysis", "ys"), ("ies", "y"), ("ing", ""),
                         ("ed", ""), ("es", ""), ("s", ""), ("e", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not (suffix == "s" and w.endswith("ss")):
            w = w[: len(w) - len(suffix)] + repl
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]  # modell -> model
    return w


def _stems(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text)]


def _has(term: str, haystack: str) -> bool:
    """Whole-word and case-blind; forgiving of plurals, verb endings, British against
    American spelling, and '&' against 'and'."""
    term = _norm(term).strip()
    if not term:
        return False
    pattern = r"(?<![\w+#])" + re.escape(term).replace(r"\ ", r"[\s/-]+") + r"(?:s|es)?(?![\w+#])"
    if re.search(pattern, haystack) is not None:
        return True
    wanted = _stems(term)
    if not wanted:
        return False
    hay = _stems(haystack)
    n = len(wanted)
    return any(hay[i:i + n] == wanted for i in range(len(hay) - n + 1))


def job_keywords(job_text: str, model_keywords: list[str] | None = None) -> list[str]:
    """What the advert asks for: the requirements the model read out of it, plus every
    known skill the advert names. Deduplicated, model's order first."""
    named = [k.strip() for k in (model_keywords or []) if k and k.strip()]
    lowered = {k.lower() for k in named}
    for skill in sorted(extract_skills(job_text or "")):
        if skill in lowered:
            continue
        if skill in _AMBIGUOUS:
            continue
        named.append(skill)
        lowered.add(skill)
    return named[:40]


def core_title(title: str) -> str:
    """"Senior Data Scientist, Payments (Hybrid)" -> "data scientist"."""
    title = re.split(r"[,(|/–—-]| at | in ", title, maxsplit=1)[0]
    return re.sub(r"\s+", " ", _SENIORITY.sub(" ", title)).strip().lower()


def score(*, text: str, headings: list[str], bullets: list[str], keywords: list[str],
          job_title: str, kind: str) -> dict:
    """Score a CV's text against a job. Returns the total, the checks, and the keyword
    matches and misses."""
    hay = _norm(text)
    words = len(text.split())
    checks = []

    def check(label: str, points: float, maximum: int, detail: str) -> None:
        status = "good" if points >= maximum * 0.8 else ("warn" if points > 0 else "bad")
        checks.append({"label": label, "points": round(points), "max": maximum,
                       "status": status, "detail": detail})

    matched = [k for k in keywords if _has(k, hay)]
    missing = [k for k in keywords if k not in matched]
    if keywords:
        share = len(matched) / len(keywords)
        check("Keywords from the advert", 45 * share, 45,
              f"{len(matched)} of the {len(keywords)} things the advert asks for appear in the CV.")
    else:
        check("Keywords from the advert", 22, 45, "The advert names few specific requirements.")

    core = core_title(job_title)
    if core and _has(core, hay):
        check("Job title", 10, 10, f'The CV uses the advert\'s own title, "{core}".')
    elif core and all(_has(w, hay) for w in core.split() if len(w) > 2):
        check("Job title", 6, 10, f'Every word of "{core}" appears, but not as the phrase itself.')
    elif core and (tail := next((" ".join(core.split()[i:]) for i in range(1, len(core.split()) - 1)
                                 if _has(" ".join(core.split()[i:]), hay)), None)):
        # "Business Data Scientist" against a CV that says "data scientist": the role
        # is there, the qualifier is not.
        check("Job title", 6, 10, f'The CV says "{tail}"; the advert\'s title is "{core}".')
    else:
        check("Job title", 0, 10, f'"{core or job_title}" does not appear anywhere in the CV.')

    heads = " | ".join(_norm(h) for h in headings)
    found = [name for name, names in _SECTIONS.items() if any(n in heads for n in names)]
    lacking = [name for name in _SECTIONS if name not in found]
    check("Standard sections", 5 * len(found), 15,
          ("Has " + ", ".join(found) + " headings" if found else "No standard headings found")
          + (f"; no heading for {', '.join(lacking)}." if lacking else ".")
          + " Parsers file what they read under headings like these.")

    contact = 0
    got = []
    if _EMAIL.search(text):
        contact += 5
        got.append("email")
    if any(sum(ch.isdigit() for ch in m.group(0)) >= 7 for m in _PHONE.finditer(text)):
        contact += 3
        got.append("phone")
    if _LINK.search(text):
        contact += 2
        got.append("a profile link")
    check("Contact details", contact, 10,
          ("Readable: " + ", ".join(got) + ".") if got else "No email or phone number could be read.")

    readable = 0
    if words >= 150:
        readable += 6
    if kind in ("pdf", "docx"):
        readable += 4
    elif kind == "txt":
        readable += 2
    check("Readable by a parser", readable, 10,
          f"{words:,} words read back out of the finished {kind.upper()} file.")

    with_numbers = [b for b in bullets if _NUMBER.search(b)]
    share = len(with_numbers) / len(bullets) if bullets else 0
    check("Measurable results", 5 if share >= 0.4 else (3 if share >= 0.2 else 0), 5,
          f"{len(with_numbers)} of {len(bullets)} bullet points carry a number.")

    if 350 <= words <= 1200:
        length = 5
    elif 250 <= words < 350 or 1200 < words <= 1600:
        length = 3
    else:
        length = 0
    check("Length", length, 5, f"{words:,} words; one to two pages, about 350 to 1,200 words, "
          "is what recruiters and parsers handle best.")

    total = sum(c["points"] for c in checks)
    return {"score": min(100, total), "checks": checks, "matched": matched, "missing": missing}
