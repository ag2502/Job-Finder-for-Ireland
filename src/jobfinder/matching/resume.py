"""Resume parsing.

Extracts the handful of signals that actually drive job matching — skills, recent
titles, seniority and years of experience — from a PDF, DOCX or plain-text CV.

Deliberately rule-based rather than model-based. Rules are inspectable, run in
milliseconds, need no API budget, and on the narrow task of "which technologies does
this document mention" they are hard to beat. The failure mode also matters: a rule that
misses a skill is a visibly absent keyword, whereas a model that hallucinates one is a
wrong match nobody can trace.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from jobfinder.normalize.taxonomy import classify_title, extract_skills

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 200_000

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead", "principal", "director"]

_SENIORITY_PATTERNS = [
    ("director", re.compile(r"\b(director|vp|vice president|head of|chief)\b", re.I)),
    ("principal", re.compile(r"\b(principal|staff engineer|distinguished)\b", re.I)),
    ("lead", re.compile(r"\b(lead|team lead|tech lead|manager)\b", re.I)),
    ("senior", re.compile(r"\b(senior|snr|sr\.?)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jnr|jr\.?|graduate|entry.level)\b", re.I)),
    ("intern", re.compile(r"\b(intern|internship|placement|trainee)\b", re.I)),
]

_YEARS_PATTERNS = [
    re.compile(r"(\d{1,2})\+?\s*years?\s+(?:of\s+)?experience", re.I),
    re.compile(r"experience\s*[:\-]\s*(\d{1,2})\+?\s*years?", re.I),
]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")

# Lines that look like a job title in an experience section.
_TITLE_LINE = re.compile(
    r"^\s*([A-Z][A-Za-z/&+.\- ]{3,60}?)\s*(?:[|@,–—-]|\bat\b)", re.M
)


@dataclass
class ParsedResume:
    text: str = ""
    skills: set[str] = field(default_factory=set)
    titles: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    seniority: str | None = None
    years_experience: int | None = None
    email: str | None = None

    def summary(self) -> str:
        return (
            f"{len(self.skills)} skills, {len(self.titles)} titles, "
            f"seniority={self.seniority}, years={self.years_experience}"
        )


def extract_text(data: bytes, filename: str) -> str:
    """Pull plain text out of a PDF, DOCX or text file."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)[:MAX_TEXT_CHARS]
        except Exception as exc:  # noqa: BLE001 - a bad upload must not crash the request
            logger.warning("PDF extraction failed for %s: %s", filename, exc)
            return ""

    if suffix in {".docx", ".doc"}:
        try:
            import docx

            document = docx.Document(io.BytesIO(data))
            parts = [p.text for p in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    parts.extend(cell.text for cell in row.cells)
            return "\n".join(parts)[:MAX_TEXT_CHARS]
        except Exception as exc:  # noqa: BLE001
            logger.warning("DOCX extraction failed for %s: %s", filename, exc)
            return ""

    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)[:MAX_TEXT_CHARS]
        except UnicodeDecodeError:
            continue
    return ""


def detect_seniority(text: str) -> str | None:
    """Return the most senior level mentioned.

    Ordered most-senior-first so "Senior Engineer reporting to the Director" resolves to
    director only if that word genuinely appears; the first pattern to hit wins.
    """
    for level, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text):
            return level
    return None


def detect_years(text: str) -> int | None:
    values: list[int] = []
    for pattern in _YEARS_PATTERNS:
        values.extend(int(m) for m in pattern.findall(text))

    # Fall back to the span between the earliest and latest four-digit years present,
    # which approximates a career length when the CV never states one.
    if not values:
        years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", text)]
        if len(years) >= 2:
            span = max(years) - min(years)
            if 0 < span <= 50:
                return span
        return None

    return max(v for v in values if 0 < v <= 50) if any(0 < v <= 50 for v in values) else None


def extract_titles(text: str, limit: int = 12) -> list[str]:
    titles: list[str] = []
    for match in _TITLE_LINE.finditer(text):
        candidate = match.group(1).strip()
        if 4 <= len(candidate) <= 60 and classify_title(candidate):
            if candidate not in titles:
                titles.append(candidate)
        if len(titles) >= limit:
            break
    return titles


def parse_resume(data: bytes, filename: str) -> ParsedResume:
    text = extract_text(data, filename)
    if not text.strip():
        return ParsedResume()

    titles = extract_titles(text)
    detected_fields: list[str] = []
    for title in titles:
        for key in classify_title(title):
            if key not in detected_fields:
                detected_fields.append(key)

    # A CV with no recognisable title lines still classifies from its body text.
    if not detected_fields:
        for key in classify_title(text[:5000]):
            if key not in detected_fields:
                detected_fields.append(key)

    email_match = _EMAIL.search(text)

    return ParsedResume(
        text=text,
        skills=extract_skills(text),
        titles=titles,
        fields=detected_fields,
        seniority=detect_seniority(text),
        years_experience=detect_years(text),
        email=email_match.group(0) if email_match else None,
    )


def parse_resume_file(path: str | Path) -> ParsedResume:
    path = Path(path)
    return parse_resume(path.read_bytes(), path.name)
