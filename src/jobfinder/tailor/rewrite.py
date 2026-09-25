"""Rewriting a CV toward one job, and revising it on the candidate's word.

The model sees the CV as numbered paragraphs, some locked, and returns replacement text
for the ones worth changing, with a reason for each, plus what the advert asks for and
where the CV falls short. It never sees the file, and cannot add, drop or reorder a
paragraph: that is what keeps the layout intact.

Its answer is then checked rather than trusted:

* edits to locked or unknown paragraphs are discarded;
* an edit that introduces a figure the CV never stated is discarded - no invented
  percentages, team sizes or years - unless the candidate gave that figure themselves;
* every surviving sentence is linted and proofread;
* the file is rebuilt and read back, and the score is taken from what an ATS would read,
  not from what the model says it wrote.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from jobfinder.core.config import settings
from jobfinder.tailor import ats, llm, proofread
from jobfinder.tailor.document import CvDocument, bold_phrases, mark, plain, text_of

MAX_JOB_CHARS = 7000

# The ATS score a tailoring aims for. Reached by truthful means only: the advert's own
# words for experience the CV shows, its job title in the profile when the experience
# fits it. What the CV cannot support stays a gap, for the candidate to confirm.
TARGET_SCORE = 85

_RULES = """\
You are an expert CV writer. You tailor a candidate's CV to one job advert so that it
passes applicant tracking systems (ATS) and reads well to a recruiter, while staying
strictly truthful.

You receive the CV as numbered paragraphs, in order. Paragraphs marked LOCKED must not be
changed. Any other paragraph may be rewritten. You cannot add, remove, merge, split or
reorder paragraphs: the CV's structure and layout stay exactly as they are.

Goal: an ATS match score of {target} or more, reached truthfully. The score counts how
many of the advert's requirements appear in the CV in the advert's own words, and
whether the CV uses the advert's job title.

How to tailor:
- When the candidate's experience fits the role, use the advert's job title (without
  its seniority or team) in the profile or summary, as a description of what they do.
- Mirror the advert's own words for skills, tools and duties wherever the CV already
  shows that experience, so an ATS keyword match finds them. Prefer the advert's exact
  phrasing ("stakeholder management", "A/B testing") over a synonym.
- Make the profile or summary speak to this role: its title, its core requirements and
  the candidate's most relevant strengths.
- In a skills line, put the most relevant items first. A skills line may gain an item
  only if the CV's own experience clearly shows it.
- Rewrite the most relevant bullets to lead with a strong verb and the outcome, keeping
  every figure exactly as it is.
- Leave paragraphs that already fit as they are. Change only what improves the match;
  a typical tailoring edits the summary, the skills lines and three to eight bullets.
- When the FORMAT line says removal is allowed, you may remove a bullet that does
  nothing for this job by returning it with empty text. Remove sparingly: never more
  than a third of the bullets, never every bullet under one role, and never a bullet
  that carries a figure or an achievement relevant to the advert. When removal is not
  allowed, make weaker bullets earn their place by wording instead.

Hard rules:
- Never invent anything: no new employers, job titles, dates, qualifications,
  certifications, tools, skills, numbers, percentages, team sizes or achievements. Every
  number in an edited paragraph must already be in the CV.
- If the advert asks for something the CV does not show, do not add it; list it under
  gaps.
- Keep each edited paragraph within its character limit. Keep the original's person,
  tense and voice, its ending punctuation (a bullet without a full stop stays without
  one) and its dash and quote style.
- {variant} English spelling throughout, matching the CV.
- Text between ** marks is set in bold in the CV. Keep the CV's own emphasis style:
  in a paragraph that has bold phrases, mark with ** the phrases in your rewrite that
  should be bold, the way the original does (typically key achievements, figures and
  technologies). Never add ** to a paragraph that had none.
- Flawless grammar, spelling and punctuation. Names of products, tools and companies
  spelled exactly as the advert or the CV spells them.
- No clichés ("results-driven", "passionate", "synergy", "go-getter") and no keyword
  stuffing: every sentence must read naturally to a person.
"""

_FIRST = _RULES + """
Also return:
- keywords: the 10 to 25 most important requirements in the advert (skills, tools,
  methods, qualifications, domain knowledge), as short phrases in the advert's own
  wording, most important first.
- fit_summary: two or three sentences addressed to the candidate ("you") on why this
  tailored CV fits the role.
- strengths: three to five short points where the CV matches the advert best.
- gaps: up to six requirements the CV does not show, each a short phrase such as
  "Kubernetes in production", so the candidate can tell us if they do have it.
- for every edit, reason: one short sentence on what the change does for this job.
"""

_REVISE = _RULES + """
This is a revision. The candidate has read the tailored CV and asked for changes.
Apply the request wherever it can be done within the rules. The candidate is the
authority on their own experience: facts they state in their request, such as a tool
they have used or a figure they give, may now be used. If part of the request cannot be
done (a new section, reordering, anything invented), do the nearest allowed thing and
explain it in reply.

Return edits against the CURRENT text shown. Include a paragraph only if you change it;
to put a paragraph back to its original wording, return that original text.

Also return:
- reply: one or two sentences to the candidate saying what you changed.
- fit_summary, strengths and gaps, updated for the revised CV, as before.
- for every edit, reason: one short sentence on what the change does.
"""

_EDIT = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "text": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["id", "text", "reason"],
}
_LIST = {"type": "array", "items": {"type": "string"}}
FIRST_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": _LIST,
        "edits": {"type": "array", "items": _EDIT},
        "fit_summary": {"type": "string"},
        "strengths": _LIST,
        "gaps": _LIST,
    },
    "required": ["keywords", "edits", "fit_summary", "strengths", "gaps"],
}
REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "edits": {"type": "array", "items": _EDIT},
        "fit_summary": {"type": "string"},
        "strengths": _LIST,
        "gaps": _LIST,
    },
    "required": ["reply", "edits", "fit_summary", "strengths", "gaps"],
}

_NUMBER = re.compile(r"\d[\d,.]*")
_REQUIREMENTS = re.compile(
    r"requirement|qualification|what you('|’)ll (bring|need|do)|about you|you (will|have|bring)|"
    r"responsibilit|skills|experience with|what we('|’)re looking|must have|nice to have",
    re.I,
)


class TailorError(RuntimeError):
    """Tailoring could not be done; the message is for the candidate."""


@dataclass
class Job:
    title: str
    company: str
    text: str


@dataclass
class Result:
    edits: dict[str, str]  # every paragraph that differs from the original, by id
    reasons: dict[str, str]
    report: dict
    data: bytes
    model: str = ""
    changes: list[dict] = field(default_factory=list)


def job_excerpt(text: str) -> str:
    """The advert, cut to what matters if it is long: the part from the first
    requirements-like heading on, with a little context before it."""
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(text) <= MAX_JOB_CHARS:
        return text
    match = _REQUIREMENTS.search(text)
    start = max(0, match.start() - 600) if match else 0
    return text[start:start + MAX_JOB_CHARS]


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in _NUMBER.findall(text)}


def _format_line(document: CvDocument) -> str:
    if document.can_remove:
        return ("FORMAT: a Word or text document that reflows, so irrelevant bullets may be "
                "removed.")
    return ("FORMAT: a PDF with a fixed layout. Nothing can be removed or added; rewrite "
            "within each paragraph's limit.")


def _paragraph_lines(document: CvDocument, current: dict[str, str], show_original: bool) -> str:
    lines = []
    for p in document.paragraphs:
        text = current.get(p.id, p.marked)
        if p.id in current and not plain(text).strip():
            lines.append(f"[{p.id}] REMOVED ({p.kind}); return its original wording to bring it "
                         f"back: {p.marked}")
            continue
        if p.locked:
            lines.append(f"[{p.id}] LOCKED ({p.kind}): {text}")
            continue
        line = f"[{p.id}] ({p.kind}, limit {p.limit} characters): {text}"
        if show_original and p.id in current:
            line += f"\n      original wording: {p.marked}"
        lines.append(line)
    return "\n".join(lines)


def _validate(document: CvDocument, answer: dict, allowed_numbers: set[str]) -> tuple[dict, dict, list]:
    """Keep the edits that follow the rules; say why the others were dropped."""
    by_id = document.by_id
    kept, reasons, dropped, removals = {}, {}, [], []
    for edit in answer.get("edits") or []:
        pid = str(edit.get("id", "")).strip().strip("[]")
        text = re.sub(r"\s+", " ", str(edit.get("text", ""))).strip()
        paragraph = by_id.get(pid)
        if paragraph is None:
            continue
        if not plain(text).strip():
            if paragraph.locked or paragraph.kind != "bullet" or not document.can_remove:
                continue
            removals.append(pid)
            reasons[pid] = str(edit.get("reason", "")).strip()
            continue
        if not paragraph.bold:
            text = plain(text)  # no bold where the original had none
        if paragraph.locked:
            dropped.append({"id": pid, "why": f"that line is kept exactly as it is ({paragraph.why_locked})"})
            continue
        invented = _numbers(plain(text)) - allowed_numbers
        if invented:
            dropped.append({"id": pid, "why": "it added a figure your CV does not state ("
                            + ", ".join(sorted(invented)) + ")"})
            continue
        if len(plain(text)) > max(paragraph.limit * 1.6, len(paragraph.text) + 60) and document.kind != "pdf":
            dropped.append({"id": pid, "why": "it grew far longer than the original"})
            continue
        kept[pid] = text
        reasons[pid] = str(edit.get("reason", "")).strip()
    # Removal is capped here as well as in the prompt: at most a third of the bullets.
    bullets = [p for p in document.paragraphs if p.kind == "bullet"]
    for pid in removals[: max(0, len(bullets) // 3)]:
        kept[pid] = ""
    for pid in removals[max(0, len(bullets) // 3):]:
        dropped.append({"id": pid, "why": "removing it as well would take out too much"})
        reasons.pop(pid, None)
    return kept, reasons, dropped


def _finish(document: CvDocument, job: Job, edits: dict[str, str], reasons: dict[str, str],
            answer: dict, keywords: list[str], dropped: list, model: str,
            language: str) -> Result:
    """Proofread, rebuild the file, read it back and score it."""
    originals = document.by_id
    # Grammar is checked on the words alone; the bold marks go back on afterwards,
    # wherever their phrases survived the corrections.
    removed = {pid for pid, text in edits.items() if not plain(text).strip()}
    phrases = {pid: bold_phrases(text) for pid, text in edits.items()}
    words = {pid: proofread.lint(plain(text), originals[pid].text)
             for pid, text in edits.items() if pid not in removed}
    words = {pid: text for pid, text in words.items() if text != originals[pid].text}
    trusted = document.text() + "\n" + job.text
    words, fixes = proofread.check(words, trusted_text=trusted, language=language)
    edits = {pid: mark(text, phrases.get(pid, [])) if originals[pid].bold else text
             for pid, text in words.items()}
    edits.update({pid: "" for pid in removed})

    rendered = document.render(edits)
    for pid, why in rendered.skipped.items():
        dropped.append({"id": pid, "why": why})
    edits = rendered.applied
    reasons = {pid: reasons.get(pid, "") for pid in edits}

    all_keywords = ats.job_keywords(job.text, keywords)

    def measure(data: bytes, applied: dict[str, str]) -> dict:
        return ats.score(
            text=text_of(data, document.kind),
            headings=[p.text for p in document.paragraphs if p.kind == "heading"],
            bullets=[plain(applied.get(p.id, p.text)) for p in document.paragraphs
                     if p.kind == "bullet" and plain(applied.get(p.id, p.text)).strip()],
            keywords=all_keywords, job_title=job.title, kind=document.kind,
        )

    before = measure(document.data, {})
    after = measure(rendered.data, edits)
    newly = [k for k in after["matched"] if k not in before["matched"]]

    changes = [
        {"id": p.id, "kind": p.kind, "before": p.text, "after": plain(edits[p.id]),
         "removed": not plain(edits[p.id]).strip(), "reason": reasons.get(p.id, "")}
        for p in document.paragraphs if p.id in edits
    ]
    report = {
        "fit_summary": str(answer.get("fit_summary", "")).strip(),
        "strengths": [s for s in (answer.get("strengths") or []) if str(s).strip()][:5],
        "gaps": [g for g in (answer.get("gaps") or []) if str(g).strip()][:6],
        "reply": str(answer.get("reply", "")).strip(),
        "keywords": keywords,
        "ats_before": before["score"],
        "ats_after": after["score"],
        "checks": after["checks"],
        "checks_before": before["checks"],
        "matched": after["matched"],
        "missing": after["missing"],
        "newly_matched": newly,
        "proofread": fixes,
        "dropped": dropped,
        "model": model,
        "language": language,
    }
    return Result(edits, reasons, report, rendered.data, model, changes)


def tailor(document: CvDocument, job: Job, *, deadline: float | None = None) -> Result:
    """The first tailoring of a CV to a job."""
    language = proofread.variant(document.text())
    user = (
        f"JOB: {job.title} at {job.company}\n\nADVERT:\n{job_excerpt(job.text)}\n\n"
        f"{_format_line(document)}\n\n"
        f"CV PARAGRAPHS:\n{_paragraph_lines(document, {}, False)}"
    )
    try:
        answer, model = llm.ask(_FIRST.format(variant=language, target=TARGET_SCORE), user, FIRST_SCHEMA,
                                name="tailored_cv", deadline=deadline)
    except llm.ModelsUnavailable as exc:
        raise TailorError(
            "The free writing service is busy right now. Please try again in a minute."
        ) from exc
    edits, reasons, dropped = _validate(document, answer, _numbers(document.text()))
    keywords = [str(k).strip() for k in (answer.get("keywords") or []) if str(k).strip()][:25]
    return _finish(document, job, edits, reasons, answer, keywords, dropped, model, language)


def revise(document: CvDocument, job: Job, current: dict[str, str], reasons: dict[str, str],
           request: str, keywords: list[str], *, earlier_requests: list[str] | None = None,
           deadline: float | None = None) -> Result:
    """Apply the candidate's request to the tailored CV they are looking at."""
    request = request.strip()[:1500]
    if not request:
        raise TailorError("Tell us what you would like changed first.")
    language = proofread.variant(document.text())
    history = "\n".join(f"- {r}" for r in (earlier_requests or [])[-4:])
    user = (
        f"JOB: {job.title} at {job.company}\n\nADVERT:\n{job_excerpt(job.text)}\n\n"
        f"{_format_line(document)}\n\n"
        f"CURRENT CV PARAGRAPHS (edited ones show their original wording too):\n"
        f"{_paragraph_lines(document, current, True)}\n\n"
        + (f"EARLIER REQUESTS, ALREADY APPLIED:\n{history}\n\n" if history else "")
        + f"THE CANDIDATE'S REQUEST NOW:\n{request}"
    )
    try:
        answer, model = llm.ask(_REVISE.format(variant=language, target=TARGET_SCORE), user, REVISE_SCHEMA,
                                name="revised_cv", deadline=deadline)
    except llm.ModelsUnavailable as exc:
        raise TailorError(
            "The free writing service is busy right now. Please try again in a minute."
        ) from exc
    allowed = _numbers(document.text()) | _numbers(request) | _numbers(" ".join(earlier_requests or []))
    changed, new_reasons, dropped = _validate(document, answer, allowed)
    merged = {**current, **changed}
    merged_reasons = {**reasons, **new_reasons}
    # A paragraph returned to its original wording is no longer an edit.
    merged = {pid: text for pid, text in merged.items() if plain(text) != document.by_id[pid].text}
    return _finish(document, job, merged, merged_reasons, answer, keywords, dropped, model, language)


def boost_request(report: dict) -> str | None:
    """What to ask for in a round aimed at the target score, from what is costing points.

    None when nothing more can be done by wording alone: the remaining points are in
    things only the candidate can supply, or in the file itself.
    """
    missing = report.get("missing") or []
    title = next((c for c in report.get("checks") or [] if c["label"] == "Job title"), None)
    asks = []
    if missing:
        asks.append(
            "These requirements from the advert are not yet in the CV in the advert's own "
            "words: " + "; ".join(missing[:15]) + ". For each one the CV's existing "
            "experience genuinely shows - the same skill in other words, a tool named in "
            "one role but not in the skills line, a duty implied by a bullet - work the "
            "advert's exact phrase into the paragraph where that experience is. Skip any "
            "the CV does not support; they stay gaps."
        )
    if title and title["points"] < title["max"]:
        asks.append(
            "The CV does not yet use the advert's job title. " + title["detail"] + " If the "
            "candidate's experience fits that role, describe them with it in the profile."
        )
    if not asks:
        return None
    return (
        f"Raise the ATS match score from {report.get('ats_after')} to {TARGET_SCORE} or more, "
        "truthfully. " + " ".join(asks) + " Keep every earlier change that still helps."
    )


def boost(document: CvDocument, job: Job, current: dict[str, str], reasons: dict[str, str],
          report: dict, *, deadline: float | None = None) -> Result | None:
    """One more round aimed at the target score, or None when there is nothing left that
    wording can fix. The caller keeps it only if the score rose."""
    request = boost_request(report)
    if request is None:
        return None
    language = proofread.variant(document.text())
    user = (
        f"JOB: {job.title} at {job.company}\n\nADVERT:\n{job_excerpt(job.text)}\n\n"
        f"{_format_line(document)}\n\n"
        f"CURRENT CV PARAGRAPHS (edited ones show their original wording too):\n"
        f"{_paragraph_lines(document, current, True)}\n\n"
        f"WHAT TO DO NOW:\n{request}"
    )
    try:
        answer, model = llm.ask(_REVISE.format(variant=language, target=TARGET_SCORE), user,
                                REVISE_SCHEMA, name="boosted_cv", deadline=deadline)
    except llm.ModelsUnavailable as exc:
        raise TailorError(
            "The free writing service is busy right now. Please try again in a minute."
        ) from exc
    # A boost never brings its own facts: only figures the CV already states.
    changed, new_reasons, dropped = _validate(document, answer, _numbers(document.text()))
    merged = {**current, **changed}
    merged = {pid: text for pid, text in merged.items() if plain(text) != document.by_id[pid].text}
    return _finish(document, job, merged, {**reasons, **new_reasons}, answer,
                   report.get("keywords") or [], dropped, model, language)


def rebuild(document: CvDocument, edits: dict[str, str]) -> bytes:
    """The finished file for a saved or accepted set of edits."""
    return document.render(edits).data


def deadline() -> float:
    return time.monotonic() + settings.tailor_timeout_seconds
