"""A cover letter drafted from the searcher's own CV and one job advert.

The same free models as tailoring (llm.py), and the same stance on facts: the letter may
say only what the CV says, pointed at what the advert asks for. Three things enforce it
rather than trusting the prompt:

* Any sentence carrying a figure (a year, a percentage, a team size) that appears in
  neither the CV nor the advert is removed before anyone sees it.
* The phrases every generated letter reaches for ("I am writing to express my
  interest", "passionate", "thrilled") are banned in the prompt and, where one slips
  through, the sentence carrying it is dropped.
* Nothing is kept on the server. The draft is shown for the person to edit, copy or
  download, and it is theirs to send or not.
"""

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass, field

from jobfinder.tailor import llm

MAX_CV_CHARS = 9000
MAX_JOB_CHARS = 7000
MAX_WORDS = 320
DEADLINE_SECONDS = 60

# Stock phrases a hiring manager has read a thousand times. Named in the prompt, and a
# sentence that still uses one is dropped.
_STOCK = re.compile(
    r"\b(?:i am writing to (?:express|apply)|express(?:ing)? my (?:strong )?interest|"
    r"passionate|thrilled|excited to apply|perfect fit|dream (?:job|role)|"
    r"hit the ground running|go-getter|synergy|i believe i would be|"
    r"look forward to hearing from you at your earliest convenience)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,.]*")

SYSTEM = """\
You draft cover letters for people applying for jobs in Ireland. You are given the
applicant's CV and one job advert. Write a short, specific letter in British English:

- 3 or 4 short paragraphs, 180 to 260 words in all.
- Open with the role applied for and one concrete reason the applicant fits it.
- In the middle, connect two or three things the CV actually shows (roles, projects,
  tools, qualifications, results) to requirements the advert actually states.
- Close with one plain sentence about being glad to talk further.
- Use ONLY facts in the CV. Never invent employers, job titles, dates, figures,
  qualifications, tools or achievements, and never claim experience the CV does not show.
  If the CV lacks something the advert asks for, leave it out rather than claim it.
- Say nothing about the employer beyond what the advert says.
- No clichés: never "I am writing to express my interest", "passionate", "thrilled",
  "excited to apply", "perfect fit", "dream job", "hit the ground running".
- No em dashes. No bullet points. No placeholders like [Company] or [Name].
- greeting: "Dear Hiring Team," unless the advert names the person to write to.
- name: the applicant's name as the CV gives it, or "" if the CV does not give one.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "name": {"type": "string"},
    },
    "required": ["greeting", "paragraphs", "name"],
}


class LetterError(RuntimeError):
    """The letter could not be written; the message is for the person who asked."""


@dataclass
class Letter:
    greeting: str
    paragraphs: list[str]
    name: str
    model: str = ""
    removed: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        sign_off = "Yours sincerely,\n" + (self.name or "")
        return "\n\n".join([self.greeting, *self.paragraphs, sign_off.strip()])

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paragraphs)


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in _NUMBER.findall(text)}


def _plain(text: str) -> str:
    text = " ".join(str(text or "").split())
    # House style: no em dashes, and no hyphen standing in for one.
    return re.sub(r"\s*—\s*|\s+[–-]\s+", ", ", text).strip()


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", paragraph) if s]


def check(answer: dict, cv_text: str, job_text: str) -> Letter:
    """The model's letter with every sentence that breaks the rules taken out."""
    allowed = _numbers(cv_text) | _numbers(job_text)
    removed: list[str] = []
    paragraphs: list[str] = []
    for raw in answer.get("paragraphs") or []:
        kept = []
        for sentence in _sentences(_plain(raw)):
            invented = _numbers(sentence) - allowed
            if invented:
                removed.append(f"a sentence with a figure your CV does not give ({', '.join(sorted(invented))})")
                continue
            if _STOCK.search(sentence) or re.search(r"\[[^\]]+\]", sentence):
                removed.append("a stock phrase")
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))
    greeting = _plain(answer.get("greeting")) or "Dear Hiring Team,"
    if not greeting.endswith(","):
        greeting += ","
    name = _plain(answer.get("name"))[:80]
    letter = Letter(greeting=greeting, paragraphs=paragraphs, name=name, removed=removed)
    if letter.words < 60:
        raise LetterError("The draft did not hold up to our checks. Try writing it again.")
    return letter


def write(cv_text: str, title: str, company: str, job_text: str, *,
          note: str = "") -> Letter:
    """Draft a letter for this job from this CV. Raises LetterError with a reason."""
    if not llm.available():
        raise LetterError("Cover letters are not switched on here yet.")
    if len(cv_text.strip()) < 200:
        raise LetterError("We could not read enough of your CV to write from. "
                          "Upload it again on your profile.")
    user = (
        f"JOB: {title}" + (f" at {company}" if company else "") + "\n\n"
        f"ADVERT:\n{job_text[:MAX_JOB_CHARS]}\n\n"
        f"CV:\n{cv_text[:MAX_CV_CHARS]}"
        + (f"\n\nTHE APPLICANT ASKS: {note.strip()[:400]}" if note.strip() else "")
    )
    try:
        answer, model = llm.ask(
            SYSTEM, user, SCHEMA, name="cover_letter", max_tokens=1500,
            deadline=time.monotonic() + DEADLINE_SECONDS,
        )
    except llm.ModelsUnavailable:
        raise LetterError("The writing models are busy just now. Please try again in a "
                          "minute.") from None
    letter = check(answer, cv_text, job_text + " " + note)
    letter.model = model
    return letter


def docx(text: str) -> bytes:
    """The letter as a Word document: plain paragraphs in the default style."""
    from docx import Document
    from docx.shared import Pt

    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    for block in text.replace("\r\n", "\n").split("\n\n"):
        document.add_paragraph(block.strip())
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()
