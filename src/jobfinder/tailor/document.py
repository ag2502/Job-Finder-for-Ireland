"""A CV as paragraphs that can be reworded without disturbing its layout.

Three formats, one shape. `load` reads a PDF, Word or text CV into `Paragraph`s, each
with an id, its text, what kind of paragraph it is and whether it may be changed at all.
`CvDocument.render` takes `{paragraph id: new text}` and writes those words back into a
copy of the original file:

* **Word** documents are edited in place, run by run, so every style, font, margin,
  table and column stays exactly as the person made it.
* **PDF** files are edited in place too, with PyMuPDF: the old words are removed from
  the page and the new ones set in the same position, size and colour, in the CV's own
  embedded font wherever that font has every letter needed, and wrapped to the same
  column. A paragraph that cannot be made to fit is left as it was and reported.
* **Plain text** is edited line by line.

Nothing here decides what the words should be. That is `rewrite`'s job; this module only
guarantees that whatever it decides lands in the right place and nowhere else.
"""

from __future__ import annotations

import base64
import copy
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt"}
MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}

BULLETS = "•●▪■◦○‣∙·➢➤►▸▶✓✔❖◆◇□-–*"
_BULLET_AT_START = re.compile(r"^\s*([•●▪■◦○‣∙·➢➤►▸▶✓✔❖◆◇□]|[-–*](?=\s))\s*")

# What makes a paragraph off limits. A CV's facts - who, where, when, how to reach them
# - are never the tailoring's to change.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S*", re.I)
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{3,4}")
# "Tools: dbt, Airflow, Looker" - a list of keywords under a label, which is exactly
# what tailoring should be able to extend.
_SKILLS_LINE = re.compile(r"^[A-Za-z][\w &/+-]{0,30}:\s*\S+.*,")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    rf"\b{_MONTH}\s*'?\d{{2,4}}\b|\b(?:19|20)\d{{2}}\b|\b(?:present|current)\b",
    re.I,
)
_PAGE = re.compile(r"\bpage\s+\d+\s*(?:of|/)\s*\d+\b", re.I)

# Section headings, recognised by name, so a paragraph knows which section it sits in.
_SECTION_NAMES = re.compile(
    r"^(?:about|profile|summary|professional summary|personal statement|objective|"
    r"(?:work |professional |relevant )?experience|employment(?: history)?|work history|"
    r"career history|education|qualifications|academic|(?:technical |key |core )?skills|"
    r"competencies|technologies|tools|projects|(?:selected )?projects|certifications?|"
    r"licen[cs]es?(?: (?:and|&) certifications)?|awards?|honou?rs|achievements|"
    r"awards (?:and|&) \w+|leadership(?: (?:and|&) \w+)?|volunteering|volunteer \w+|"
    r"publications|patents|languages|interests|hobbies|references|activities)\s*:?$",
    re.I,
)
# Sections whose entries are facts - a degree, a certificate, an award - rather than
# wording, and so are never reworded.
_FACT_SECTIONS = re.compile(
    r"educat|qualific|academic|certif|licen[cs]|award|honou?r|publicat|patent|reference|"
    r"^languages$|interests|hobbies",
    re.I,
)

# Bold inside a paragraph travels as **markdown-style** marks: the model sees which
# phrases the CV sets in bold and marks its own rewrite the same way, and the renderers
# set those phrases bold again.
_MARK = re.compile(r"\*\*(.+?)\*\*")


def plain(text: str) -> str:
    """A paragraph's words without its bold marks."""
    return _MARK.sub(r"\1", text).replace("**", "")


def bold_phrases(text: str) -> list[str]:
    return [m.group(1).strip() for m in _MARK.finditer(text) if m.group(1).strip()]


def _bold_mask(text: str, phrases: list[str]) -> list[bool]:
    """Which characters of `text` fall inside one of the bold phrases."""
    mask = [False] * len(text)
    for phrase in sorted({p for p in phrases if len(p) >= 2}, key=len, reverse=True):
        start = text.find(phrase)
        while start >= 0:
            for i in range(start, start + len(phrase)):
                mask[i] = True
            start = text.find(phrase, start + len(phrase))
    return mask


def mark(text: str, phrases: list[str]) -> str:
    """`text` with each of `phrases` wrapped in bold marks, wherever it still appears."""
    mask = _bold_mask(text, phrases)
    out, bold = [], False
    for i, ch in enumerate(text):
        on = mask[i] and not (ch == " " and (i + 1 >= len(text) or not mask[i + 1]))
        if on and not bold:
            out.append("**")
            bold = True
        elif not on and bold:
            out.append("**")
            bold = False
        out.append(ch)
    if bold:
        out.append("**")
    return "".join(out).replace("** **", " ")


@dataclass
class Paragraph:
    id: str
    text: str
    kind: str  # "heading", "bullet" or "text"
    locked: bool
    limit: int  # the most characters a rewrite may use
    why_locked: str = ""
    # Format-specific handles, kept out of anything serialised.
    _ref: object = field(default=None, repr=False)
    # The text with its bold phrases marked, as the model sees it.
    marked: str = ""

    def __post_init__(self) -> None:
        if not self.marked:
            self.marked = self.text

    @property
    def bold(self) -> list[str]:
        return bold_phrases(self.marked)


@dataclass
class Rendered:
    data: bytes
    applied: dict[str, str]
    skipped: dict[str, str]  # paragraph id -> why it was left as it was


class DocumentError(ValueError):
    """The file could not be read as a CV."""


def kind_of(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower()
    return KINDS.get(suffix)


def _classify(text: str, *, index: int, bold: bool, big: bool, styled_heading: bool,
              bullet: bool, italic: bool = False) -> tuple[str, bool, str]:
    """(kind, locked, why) for a paragraph, from its text and what its formatting says."""
    words = text.split()
    stripped = text.strip()
    heading = (
        styled_heading
        or (
            len(words) <= 6
            and not stripped.endswith(".")
            and (bold or big or (stripped.isupper() and len(stripped) > 2) or stripped.endswith(":"))
        )
    )
    kind = "bullet" if bullet else ("heading" if heading and not bullet else "text")
    if kind == "heading":
        return kind, True, "a heading"
    if index == 0 and len(words) <= 5:
        return kind, True, "your name"
    if _EMAIL.search(text) or _URL.search(text):
        return kind, True, "contact details"
    if _PHONE.search(text) and sum(ch.isdigit() for ch in text) >= 7:
        return kind, True, "contact details"
    if _PAGE.search(text):
        return kind, True, "a page footer"
    # A date line - "Mar 2021 - Present", "Data Scientist, Acme (2019-2023)" - is a fact.
    # A long paragraph that happens to mention a year is not, and stays editable: the
    # rule that no figure may be added or changed protects the year inside it.
    if _DATE.search(text) and len(words) <= 14:
        return kind, True, "dates"
    if len(words) <= 3 and not _SKILLS_LINE.match(stripped):
        return kind, True, "a label"
    # A short line with no sentence ending is a job title, an employer or a degree -
    # "Data Scientist, Fintech Co" - which is a fact, not wording.
    skills_line = bool(_SKILLS_LINE.match(stripped))
    if len(words) <= 6 and not stripped.endswith((".", "!", "?")) and kind != "bullet" \
            and not skills_line:
        return kind, True, "a title or label"
    # "Software Engineer | Payments Platform", or a role set in italics under its
    # employer: a title line, however long.
    if (len(words) <= 16 and not stripped.endswith((".", "!", "?")) and kind != "bullet"
            and not skills_line and (" | " in stripped or italic)):
        return kind, True, "a title or label"
    return kind, False, ""


class CvDocument:
    kind: str
    name: str
    data: bytes
    paragraphs: list[Paragraph]

    def __init__(self, kind: str, name: str, data: bytes, paragraphs: list[Paragraph]):
        self.kind, self.name, self.data, self.paragraphs = kind, name, data, paragraphs

    @property
    def by_id(self) -> dict[str, Paragraph]:
        return {p.id: p for p in self.paragraphs}

    def text(self, edits: dict[str, str] | None = None) -> str:
        edits = edits or {}
        return "\n".join(plain(edits.get(p.id, p.text)) for p in self.paragraphs)

    @property
    def can_remove(self) -> bool:
        """Whether a paragraph can be taken out without leaving a hole. Word and text
        files reflow; a PDF does not, so there a line can only be reworded."""
        return self.kind in ("docx", "txt")

    def render(self, edits: dict[str, str]) -> Rendered:
        edits = {k: v for k, v in edits.items() if k in self.by_id and not self.by_id[k].locked
                 and (plain(v).strip() or self.can_remove)}
        if self.kind == "pdf":
            return _render_pdf(self, edits)
        if self.kind == "docx":
            return _render_docx(self, edits)
        return _render_txt(self, edits)

    def previews(self, rendered: bytes, changed: list[str], *, dpi: int = 96) -> list[str]:
        """The finished document's pages as PNG data URIs, changes lightly marked.

        Only PDFs are drawn as pages; a Word file has no rendering here that would look
        like Word, and a text file is its own preview.
        """
        if self.kind != "pdf":
            return []
        import pymupdf

        doc = pymupdf.open(stream=rendered, filetype="pdf")
        marks = _pdf_paragraph_rects(self, changed)
        out = []
        for number, page in enumerate(doc):
            for rect in marks.get(number, []):
                page.draw_rect(rect, color=None, fill=(1, 0.85, 0.29), fill_opacity=0.28,
                               overlay=True)
            pixmap = page.get_pixmap(dpi=dpi)
            out.append("data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode())
        return out


def load(data: bytes, filename: str) -> CvDocument:
    kind = kind_of(filename)
    if kind is None:
        raise DocumentError("Tailoring works on PDF, Word (.docx) and plain text CVs.")
    try:
        if kind == "pdf":
            paragraphs = _read_pdf(data)
        elif kind == "docx":
            paragraphs = _read_docx(data)
        else:
            paragraphs = _read_txt(data)
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - a broken upload must not crash a request
        raise DocumentError("That CV could not be opened. Is the file damaged?") from exc
    _lock_fact_sections(paragraphs)
    if sum(len(p.text.split()) for p in paragraphs) < 30:
        raise DocumentError(
            "There is almost no text in that CV to work with. A scan or a photo of a CV "
            "cannot be tailored; a PDF or Word file saved from a word processor can."
        )
    return CvDocument(kind, filename, data, paragraphs)


def _lock_fact_sections(paragraphs: list[Paragraph]) -> None:
    """Lock everything under Education, Certifications, Awards and the like."""
    section, column = "", None
    for paragraph in paragraphs:
        name = plain(paragraph.text).strip()
        # A new column is a new part of the page; the sidebar's last section does not
        # carry on into the main column.
        ref = paragraph._ref
        here = (ref.page, ref.gutter is not None and ref.pieces[0].bbox[0] >= ref.gutter) \
            if isinstance(ref, _PdfPara) else None
        if here != column:
            section, column = "", here
        if paragraph.kind == "heading" and _SECTION_NAMES.match(name):
            section = name
            continue
        if section and not paragraph.locked and _FACT_SECTIONS.search(section):
            paragraph.locked = True
            paragraph.why_locked = f"part of your {section.lower().rstrip(':')}"


# --------------------------------------------------------------------------- text


def _read_txt(data: bytes) -> list[Paragraph]:
    text = _decode(data)
    paragraphs = []
    for number, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        bullet = bool(_BULLET_AT_START.match(line))
        body = _BULLET_AT_START.sub("", line).strip()
        kind, locked, why = _classify(body, index=len(paragraphs), bold=False, big=False,
                                      styled_heading=False, bullet=bullet)
        paragraphs.append(Paragraph(f"p{len(paragraphs)}", body, kind, locked,
                                    int(len(body) * 1.35) + 20, why, _ref=number))
    return paragraphs


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _render_txt(document: CvDocument, edits: dict[str, str]) -> Rendered:
    text = _decode(document.data)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    removed = set()
    for paragraph in document.paragraphs:
        if paragraph.id not in edits:
            continue
        if not plain(edits[paragraph.id]).strip():
            removed.add(paragraph._ref)
            continue
        line = lines[paragraph._ref]
        prefix = _BULLET_AT_START.match(line)
        indent = line[: len(line) - len(line.lstrip())]
        lines[paragraph._ref] = (prefix.group(0) if prefix else indent) + plain(edits[paragraph.id])
    lines = [line for n, line in enumerate(lines) if n not in removed]
    return Rendered(newline.join(lines).encode("utf-8"), dict(edits), {})


# --------------------------------------------------------------------------- word

_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _docx_paragraphs(document):
    """Every paragraph in reading order: body, tables and text boxes, once each.

    Text boxes are written twice in a .docx, once for Word and once as a fallback for
    older readers; the fallback copy is skipped so no paragraph is edited twice.
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as DocxParagraph

    for element in document.element.body.iter(qn("w:p")):
        if any(a.tag == f"{_MC}Fallback" for a in element.iterancestors()):
            continue
        yield DocxParagraph(element, document)


def _read_docx(data: bytes) -> list[Paragraph]:
    import docx
    from docx.oxml.ns import qn

    document = docx.Document(io.BytesIO(data))
    paragraphs = []
    for index, para in enumerate(_docx_paragraphs(document)):
        text = para.text
        if not text.strip():
            continue
        style = (para.style.name if para.style is not None else "") or ""
        runs = [r for r in para.runs if r.text.strip()]
        bold = bool(runs) and all(_run_bold(r, para) for r in runs)
        numbered = para._p.pPr is not None and para._p.pPr.numPr is not None
        bullet = numbered or "List" in style or bool(_BULLET_AT_START.match(text))
        body = _BULLET_AT_START.sub("", text).strip() if not numbered else text.strip()
        italic = bool(runs) and all(r.italic for r in runs)
        kind, locked, why = _classify(
            body, index=len(paragraphs), bold=bold, big=False,
            styled_heading=style.startswith(("Heading", "Title")), bullet=bullet, italic=italic,
        )
        el = para._p
        if not locked and (el.findall(qn("w:hyperlink")) or list(el.iter(qn("w:instrText")))
                           or el.findall(qn("w:fldSimple"))):
            locked, why = True, "a link or field"
        paragraphs.append(Paragraph(f"p{len(paragraphs)}", body, kind, locked,
                                    int(len(body) * 1.35) + 20, why, _ref=index,
                                    marked=_docx_marked(para, body, numbered)))
    return paragraphs


def _docx_marked(para, body: str, numbered: bool) -> str:
    """The paragraph's text with bold runs marked, when most of it is not bold."""
    pieces = [(r.text, _run_bold(r, para)) for r in para.runs if r.text]
    if not pieces or all(b for t, b in pieces if t.strip()):
        return body
    text = "".join(t for t, _ in pieces)
    phrases = [t.strip() for t, b in pieces if b and t.strip()]
    marked = mark(text, phrases)
    marked = _BULLET_AT_START.sub("", marked).strip() if not numbered else marked.strip()
    return marked if plain(marked) == body else body


def _run_bold(run, para) -> bool:
    if run.bold is not None:
        return bool(run.bold)
    style = para.style
    while style is not None:
        if style.font is not None and style.font.bold is not None:
            return bool(style.font.bold)
        style = style.base_style
    return False


def _set_paragraph_text(para, new_text: str, original_bold: list[str] | None = None) -> None:
    """Replace a paragraph's words, keeping how it looks.

    Leading runs whose text the new wording still begins with - a bold "Languages:",
    a bullet typed as a character, a tab - are kept as they are. The rest of the words
    go into one run carrying the formatting that most of the old words had, so a
    sentence that was mostly regular with one bold word comes back regular rather than
    taking on whichever formatting happened to come first.
    """
    phrases = bold_phrases(new_text) + list(original_bold or [])
    new_text = plain(new_text)
    mask = _bold_mask(new_text, phrases)
    runs = list(para.runs)
    if not runs:
        para.add_run(new_text)
        return

    kept, consumed = 0, ""
    for run in runs:
        candidate = consumed + run.text
        # A run is kept only while it is a real prefix and differs in formatting from
        # the body - keeping body-formatted runs gains nothing and splits the text.
        if run.text and new_text.startswith(candidate) and len(candidate) < len(new_text):
            kept += 1
            consumed = candidate
        else:
            break

    body_runs = runs[kept:]
    if not body_runs:
        runs[-1].text += new_text[len(consumed):]
        return
    dominant = max(body_runs, key=lambda r: len(r.text))
    target = body_runs[0]
    if target is not dominant:
        rpr = dominant._r.rPr
        if target._r.rPr is not None:
            target._r.remove(target._r.rPr)
        if rpr is not None:
            target._r.insert(0, copy.deepcopy(rpr))
    remainder = new_text[len(consumed):]
    remainder_mask = mask[len(consumed):]
    # A leading bullet typed as text stays; the model is given the words without it.
    bullet = _BULLET_AT_START.match(para.text)
    if kept == 0 and bullet and not _BULLET_AT_START.match(remainder):
        remainder = bullet.group(0) + remainder
        remainder_mask = [False] * len(bullet.group(0)) + remainder_mask
    bold_rpr = next((r._r.rPr for r in body_runs if r.bold and r._r.rPr is not None), None)
    for run in body_runs[1:]:
        run._r.getparent().remove(run._r)

    # One run per stretch of bold or regular text, each carrying the formatting its
    # kind had in the original paragraph.
    segments, start = [], 0
    for i in range(1, len(remainder) + 1):
        if i == len(remainder) or remainder_mask[i] != remainder_mask[start]:
            segments.append((remainder[start:i], remainder_mask[start] if remainder else False))
            start = i
    target.text = segments[0][0] if segments else ""
    previous = target._r
    base_rpr = copy.deepcopy(target._r.rPr) if target._r.rPr is not None else None
    if segments and segments[0][1]:
        _make_bold(target, bold_rpr)
    for text, is_bold in segments[1:]:
        element = copy.deepcopy(previous)
        previous.addnext(element)
        previous = element
        from docx.text.run import Run

        run = Run(element, para)
        if run._r.rPr is not None:
            run._r.remove(run._r.rPr)
        if base_rpr is not None:
            run._r.insert(0, copy.deepcopy(base_rpr))
        run.text = text
        if is_bold:
            _make_bold(run, bold_rpr)


def _make_bold(run, bold_rpr) -> None:
    if bold_rpr is not None:
        if run._r.rPr is not None:
            run._r.remove(run._r.rPr)
        run._r.insert(0, copy.deepcopy(bold_rpr))
    else:
        run.bold = True


def _render_docx(document: CvDocument, edits: dict[str, str]) -> Rendered:
    import docx

    doc = docx.Document(io.BytesIO(document.data))
    paras = list(_docx_paragraphs(doc))
    for paragraph in document.paragraphs:
        if paragraph.id not in edits:
            continue
        element = paras[paragraph._ref]._p
        if not plain(edits[paragraph.id]).strip():
            element.getparent().remove(element)  # Word reflows; nothing is left behind
            continue
        _set_paragraph_text(paras[paragraph._ref], edits[paragraph.id], paragraph.bold)
    out = io.BytesIO()
    doc.save(out)
    return Rendered(out.getvalue(), dict(edits), {})


# ---------------------------------------------------------------------------- pdf


@dataclass
class _Piece:
    """One visual line of a paragraph, or one tab-separated part of a line."""

    page: int
    chars: list[dict]  # rawdict chars: {"c", "bbox", "origin"}
    spans: list[dict]  # the spans the chars came from, one per char, same order
    bbox: tuple[float, float, float, float]
    baseline: float
    size: float

    @property
    def text(self) -> str:
        return "".join(c["c"] for c in self.chars)


@dataclass
class _PdfPara:
    page: int
    pieces: list[_Piece]
    bullet_chars: int  # how many leading chars are the bullet and its spacing
    drawn_bullet: bool = False  # a bullet drawn as a shape, left of the text
    gutter: float | None = None  # the page's column split, when it has two columns
    column_right: float = 0.0
    bottom_limit: float = 0.0


def _span_style(span: dict) -> tuple[str, float, int, int]:
    return (span["font"], round(span["size"], 1), span["flags"], span["color"])


def _pdf_pieces(page, number: int) -> list[_Piece]:
    import pymupdf

    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    pieces: list[_Piece] = []
    for block in raw["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            if abs(line["dir"][1]) > 0.01:  # rotated text is decoration, never body copy
                continue
            chars, spans = [], []
            for span in line["spans"]:
                for ch in span["chars"]:
                    chars.append(ch)
                    spans.append(span)
            # Split where a line jumps a long way right: a job title and its dates set
            # on one line with a tab between them are two different things.
            start = 0
            for i in range(1, len(chars) + 1):
                gap = (
                    i < len(chars)
                    and chars[i]["bbox"][0] - chars[i - 1]["bbox"][2] > spans[i]["size"] * 1.6
                )
                if i == len(chars) or gap:
                    part, part_spans = chars[start:i], spans[start:i]
                    while part and not part[-1]["c"].strip():
                        part, part_spans = part[:-1], part_spans[:-1]
                    while part and not part[0]["c"].strip():
                        part, part_spans = part[1:], part_spans[1:]
                    if part:
                        x0 = min(c["bbox"][0] for c in part)
                        x1 = max(c["bbox"][2] for c in part)
                        y0 = min(s["bbox"][1] for s in part_spans)
                        y1 = max(s["bbox"][3] for s in part_spans)
                        size = max(s["size"] for s in part_spans)
                        pieces.append(_Piece(number, part, part_spans, (x0, y0, x1, y1),
                                             part[0]["origin"][1], size))
                    start = i
    pieces.sort(key=lambda p: (round(p.baseline, 1), p.bbox[0]))
    return pieces


def _bullet_prefix(piece: _Piece) -> int:
    text = piece.text
    match = _BULLET_AT_START.match(text)
    if match:
        return len(match.group(0))
    # A bullet drawn from a symbol font arrives as an unmapped glyph.
    if text and (text[0] in "" or ord(text[0]) in range(0xE000, 0xF900)):
        n = 1
        while n < len(text) and not text[n].strip():
            n += 1
        return n
    return 0


def _drawn_bullets(page) -> list[tuple[float, float, float, float]]:
    """Small filled marks - discs and squares - that browsers and some word processors
    draw as shapes rather than set as a bullet character."""
    marks = []
    for item in page.get_drawings():
        rect = item.get("rect")
        if rect is None or item.get("fill") is None:
            continue
        if 1.5 <= rect.width <= 7 and 1.5 <= rect.height <= 7 and abs(rect.width - rect.height) < 1.5:
            marks.append((rect.x0, rect.y0, rect.x1, rect.y1))
    return marks


def _inline_art(page) -> list[tuple[float, float, float, float]]:
    """Every piece of line art small enough to sit inside a line of text."""
    out = []
    for item in page.get_drawings():
        rect = item.get("rect")
        if rect is not None and rect.width < 40 and rect.height < 14:
            out.append((rect.x0, rect.y0, rect.x1, rect.y1))
    return out


def _art_over_text(para: "_PdfPara", art) -> bool:
    """Whether something drawn sits on this paragraph's words - the strokes of a euro
    sign built from a C, an underline - which would stay put if the words moved."""
    for i, piece in enumerate(para.pieces):
        chars = piece.chars[para.bullet_chars if i == 0 else 0:]
        if not chars:
            continue
        x0 = min(c["bbox"][0] for c in chars)
        x1 = max(c["bbox"][2] for c in chars)
        y0, y1 = piece.bbox[1], piece.bbox[3]
        if any(ax0 < x1 and ax1 > x0 and ay0 < y1 and ay1 > y0 for ax0, ay0, ax1, ay1 in art):
            return True
    return False


def _has_drawn_bullet(piece: _Piece, marks) -> bool:
    x0, y0, _x1, y1 = piece.bbox
    return any(mx1 <= x0 + 1 and mx1 >= x0 - 22 and my0 < y1 and my1 > y0
               for mx0, my0, mx1, my1 in marks)


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower() or "black" in span["font"].lower()


def _group_pdf(pieces: list[_Piece], marks: dict[int, list]) -> list[_PdfPara]:
    """Lines into paragraphs: a bullet, a change of style, or a gap starts a new one."""
    paras: list[_PdfPara] = []
    # Body size: the size most characters are set in.
    sizes: dict[float, int] = {}
    for p in pieces:
        sizes[round(p.size, 1)] = sizes.get(round(p.size, 1), 0) + len(p.chars)
    body_size = max(sizes, key=sizes.get) if sizes else 10.0

    by_column: list[_PdfPara] = []
    for piece in pieces:
        bullet = _bullet_prefix(piece)
        drawn = not bullet and _has_drawn_bullet(piece, marks.get(piece.page, []))
        # A line that starts with a bold label - "Tools:" - or that is bold throughout
        # where the line before was not, starts a new paragraph. A bold phrase inside a
        # sentence that happens to begin a line does not.
        label = bool(re.match(r"^[A-Z][\w &/+.-]{0,30}:", piece.text)) and _is_bold(piece.spans[0])
        all_bold = all(_is_bold(s) for s in piece.spans)
        joined = None
        if not bullet and not drawn:
            for para in reversed(by_column[-6:]):
                last = para.pieces[-1]
                if last.page != piece.page:
                    continue
                last_bold = all(_is_bold(s) for s in last.spans)
                gap = piece.baseline - last.baseline
                text_x = (last.chars[para.bullet_chars]["bbox"][0]
                          if len(para.pieces) == 1 and para.bullet_chars < len(last.chars)
                          else last.bbox[0])
                overlaps = piece.bbox[0] < last.bbox[2] and piece.bbox[2] > last.bbox[0]
                if (overlaps and 0 < gap <= last.size * 1.75 and not label
                        and all_bold == last_bold and abs(piece.size - last.size) < 0.6
                        and abs(piece.bbox[0] - text_x) <= max(3.0, piece.size * 0.8)):
                    joined = para
                    break
        if joined is not None:
            joined.pieces.append(piece)
        else:
            para = _PdfPara(piece.page, [piece], bullet, drawn_bullet=drawn)
            by_column.append(para)
            paras.append(para)
    paras.sort(key=lambda p: (p.page, p.pieces[0].baseline, p.pieces[0].bbox[0]))
    return paras, body_size


def _pdf_text(para: _PdfPara) -> tuple[str, str]:
    """(plain text, text with its bold phrases marked) for a paragraph."""
    chars: list[tuple[str, bool]] = []
    for n, piece in enumerate(para.pieces):
        start = para.bullet_chars if n == 0 else 0
        items = [(c["c"], _is_bold(s)) for c, s in zip(piece.chars[start:], piece.spans[start:])]
        while items and not items[0][0].strip():
            items.pop(0)
        while items and not items[-1][0].strip():
            items.pop()
        if not items:
            continue
        if chars and not (chars[-1][0] == "-" and items[0][0].islower()):
            chars.append((" ", False))
        chars.extend(items)
    out: list[tuple[str, bool]] = []
    for c, b in chars:
        if not c.strip():
            if out and out[-1][0] == " ":
                continue
            out.append((" ", False))
        else:
            out.append((c, b))
    while out and out[0][0] == " ":
        out.pop(0)
    while out and out[-1][0] == " ":
        out.pop()
    text = "".join(c for c, _ in out)
    letters = [b for c, b in out if c != " "]
    if not letters or all(letters):
        return text, text
    marked, bold = [], False
    for i, (c, b) in enumerate(out):
        # A space between two bold words is part of the phrase.
        if c == " ":
            b = bold and i + 1 < len(out) and out[i + 1][1]
        if b and not bold:
            marked.append("**")
        elif not b and bold:
            marked.append("**")
        bold = b
        marked.append(c)
    if bold:
        marked.append("**")
    return text, "".join(marked)


def _gutter(paras: list[_PdfPara], width: float) -> float | None:
    """Where a page splits into two columns, or None for a single column.

    A gutter is a vertical band, in the middle two thirds of the page, that no line of
    text crosses, with a real share of the text on each side. Lines that run across the
    whole page - a banner with the name - are ignored when looking for it.
    """
    pieces = [pc for para in paras for pc in para.pieces
              if pc.bbox[2] - pc.bbox[0] < width * 0.6]
    if len(pieces) < 6:
        return None
    covered = [0] * (int(width) + 2)
    for pc in pieces:
        for x in range(max(0, int(pc.bbox[0])), min(len(covered), int(pc.bbox[2]) + 1)):
            covered[x] += 1
    best, run_start = None, None
    lo, hi = int(width * 0.17), int(width * 0.83)
    for x in range(lo, hi + 1):
        empty = covered[x] == 0
        if empty and run_start is None:
            run_start = x
        if (not empty or x == hi) and run_start is not None:
            end = x if not empty else x + 1
            if end - run_start >= 6 and (best is None or end - run_start > best[1] - best[0]):
                best = (run_start, end)
            run_start = None
    if best is None:
        return None
    split = (best[0] + best[1]) / 2
    left = sum(len(pc.chars) for pc in pieces if pc.bbox[2] <= split)
    right = sum(len(pc.chars) for pc in pieces if pc.bbox[0] >= split)
    total = left + right or 1
    if min(left, right) / total < 0.12:
        return None
    return split


def _reading_order(paras: list[_PdfPara], page_rects: dict[int, tuple]) -> list[_PdfPara]:
    """Paragraphs in the order a person reads them: page by page, and on a page with
    two columns, the left column top to bottom, then the right. Anything that spans
    both columns stays where it sits and separates what comes before from after."""
    out = []
    for number in sorted({p.page for p in paras}):
        on_page = sorted((p for p in paras if p.page == number),
                         key=lambda p: (p.pieces[0].baseline, p.pieces[0].bbox[0]))
        width = page_rects[number][2]
        split = _gutter(on_page, width)
        if split is None:
            out.extend(on_page)
            continue
        band: list[list[_PdfPara]] = [[], []]
        for para in on_page:
            x0 = min(pc.bbox[0] for pc in para.pieces)
            x1 = max(pc.bbox[2] for pc in para.pieces)
            para.gutter = split
            if x0 < split < x1:
                out.extend(band[0] + band[1])
                band = [[], []]
                para.gutter = None
                out.append(para)
            else:
                band[0 if x1 <= split else 1].append(para)
        out.extend(band[0] + band[1])
    return out


def _layout_limits(paras: list[_PdfPara], page_rects: dict[int, tuple]) -> None:
    """How far right each paragraph may run, and how far down, before it meets something."""
    for para in paras:
        x0 = para.pieces[0].bbox[0]
        right = max(p.bbox[2] for p in para.pieces)
        # The column's right edge: the furthest any line reaches that starts inside
        # this paragraph's own span of the page.
        for other in paras:
            if other.page != para.page:
                continue
            for p in other.pieces:
                if x0 - 2 <= p.bbox[0] < right:
                    right = max(right, p.bbox[2])
        para.column_right = min(right, page_rects[para.page][2] - 18)
        # Never across the gutter into the next column.
        if para.gutter is not None and x0 < para.gutter:
            para.column_right = min(para.column_right, para.gutter - 4)
        bottom = page_rects[para.page][3] - 30
        last = para.pieces[-1]
        for other in paras:
            if other is para or other.page != para.page:
                continue
            top = other.pieces[0].bbox[1]
            ox0, ox1 = other.pieces[0].bbox[0], max(p.bbox[2] for p in other.pieces)
            if top > last.bbox[1] + 0.5 and ox0 < para.column_right and ox1 > x0:
                bottom = min(bottom, top - 1)
        para.bottom_limit = bottom


def _read_pdf(data: bytes) -> list[Paragraph]:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.is_encrypted:
        raise DocumentError("That PDF is password-protected. Upload a copy without a password.")
    pieces = []
    rects, marks, art = {}, {}, {}
    for number, page in enumerate(doc):
        rects[number] = tuple(page.rect)
        marks[number] = _drawn_bullets(page)
        art[number] = [a for a in _inline_art(page) if a not in marks[number]]
        pieces.extend(_pdf_pieces(page, number))
    paras, body_size = _group_pdf(pieces, marks)
    paras = _reading_order(paras, rects)
    _layout_limits(paras, rects)

    out = []
    for para in paras:
        text, marked = _pdf_text(para)
        if not text:
            continue
        first = para.pieces[0]
        body_spans = first.spans[para.bullet_chars:] or first.spans
        all_spans = body_spans + [s for p in para.pieces[1:] for s in p.spans]
        bold = all(_is_bold(s) for s in body_spans)
        italic = all(bool(s["flags"] & 2) or "italic" in s["font"].lower() for s in all_spans)
        big = first.size >= body_size + 1.5
        kind, locked, why = _classify(text, index=len(out), bold=bold, big=big,
                                      styled_heading=False, italic=italic,
                                      bullet=para.bullet_chars > 0 or para.drawn_bullet)
        if not locked and _art_over_text(para, art[para.page]):
            locked, why = True, "drawn marks (a symbol or an underline) that would not move with the words"
        used = sum(p.bbox[2] - p.bbox[0] for p in para.pieces) or 1
        line_h = _line_height(para)
        room_lines = max(len(para.pieces),
                         int((para.bottom_limit - first.baseline) / line_h) + 1)
        width = para.column_right - _text_x(para, first=False)
        ratio = min(1.3, max(1.0, room_lines * width / used * 0.92))
        limit = int(len(text) * ratio)
        out.append(Paragraph(f"p{len(out)}", text, kind, locked, limit, why, _ref=para,
                             marked=marked))
    return out


def _text_x(para: _PdfPara, *, first: bool) -> float:
    lead = para.pieces[0]
    if first or len(para.pieces) == 1:
        index = min(para.bullet_chars, len(lead.chars) - 1)
        return lead.chars[index]["bbox"][0]
    return para.pieces[1].bbox[0]


def _line_height(para: _PdfPara) -> float:
    gaps = [b.baseline - a.baseline for a, b in zip(para.pieces, para.pieces[1:])
            if b.baseline > a.baseline]
    if gaps:
        return sorted(gaps)[len(gaps) // 2]
    return para.pieces[0].size * 1.22


def _pdf_paragraph_rects(document: CvDocument, ids: list[str]):
    import pymupdf

    marks: dict[int, list] = {}
    for paragraph in document.paragraphs:
        if paragraph.id not in ids:
            continue
        para: _PdfPara = paragraph._ref
        x0 = _text_x(para, first=True) - 2
        y0 = para.pieces[0].bbox[1] - 1
        y1 = para.pieces[-1].bbox[3] + 1
        marks.setdefault(para.page, []).append(pymupdf.Rect(x0, y0, para.column_right + 2, y1))
    return marks


_SERIF = ("times", "georgia", "garamond", "cambria", "palatino", "book", "serif", "baskerville",
          "minion", "caslon", "didot", "charter")


def _fallback_font(span: dict):
    import pymupdf

    name = span["font"].lower()
    bold, italic = _is_bold(span), bool(span["flags"] & 2) or "italic" in name or "oblique" in name
    serif = any(s in name for s in _SERIF) and "sans" not in name
    if serif:
        code = {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit",
                (True, True): "tibi"}[(bold, italic)]
    else:
        code = {(False, False): "helv", (True, False): "hebo", (False, True): "heit",
                (True, True): "hebi"}[(bold, italic)]
    return pymupdf.Font(code)


class _Fonts:
    """The CV's own embedded fonts, by the name the text spans use.

    A PDF often embeds one font several times, each copy holding only the letters used
    near it (Chrome starts a new subset every few hundred glyphs). New words can use a
    copy only if it holds every letter they need, so all copies are kept and tried.
    """

    def __init__(self, doc):
        import pymupdf

        self._pymupdf = pymupdf
        self.doc = doc
        self.xrefs: dict[str, list[int]] = {}
        self.loaded: dict[int, object | None] = {}
        seen = set()
        for page in doc:
            for xref, _ext, _type, basefont, *_ in page.get_fonts(full=True):
                if xref in seen:
                    continue
                seen.add(xref)
                self.xrefs.setdefault(basefont.split("+", 1)[-1], []).append(xref)
        self.forced_fallback: set[str] = set()

    def _load(self, xref: int):
        if xref not in self.loaded:
            font = None
            try:
                _base, ext, _sub, buffer = self.doc.extract_font(xref)
                if buffer and ext not in ("n/a", ""):
                    font = self._pymupdf.Font(fontbuffer=buffer)
            except Exception:  # noqa: BLE001 - an unreadable font means the fallback
                font = None
            self.loaded[xref] = font
        return self.loaded[xref]

    def own(self, name: str) -> list:
        xrefs = self.xrefs.get(name) or next(
            (x for n, x in self.xrefs.items() if n.endswith(name) or name.endswith(n)), []
        )
        return [f for f in (self._load(x) for x in xrefs) if f is not None]

    def for_word(self, span: dict, word: str, key: str = ""):
        """The CV's own font for this word when a copy of it has every letter; otherwise
        the closest standard font, for this word alone. Choosing per word keeps a
        paragraph in the CV's own typeface even when one new word needs a letter the
        embedded copy never included."""
        if key not in self.forced_fallback:
            needed = {ord(c) for c in word if c.strip()}
            for font in self.own(span["font"]):
                if all(font.has_glyph(c) for c in needed):
                    return font
        return _fallback_font(span)

    def for_text(self, span: dict, text: str, key: str = ""):
        """The CV's own font if a copy of it can draw every letter, else a close standard one."""
        if key not in self.forced_fallback:
            needed = {ord(c) for c in text if c.strip()}
            for font in self.own(span["font"]):
                if all(font.has_glyph(c) for c in needed):
                    return font
        return _fallback_font(span)


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)


def _plan_paragraph(paragraph: Paragraph, new_marked: str, fonts: _Fonts, key: str = ""):
    """Where each new word goes, in which font, or None if they cannot fit.

    Words are laid out one by one so a bold phrase inside a sentence - a figure, a
    technology - is set bold as it was in the original, in the CV's own bold font.
    """
    para: _PdfPara = paragraph._ref
    text = re.sub(r"\s+", " ", plain(new_marked)).strip()
    mask = _bold_mask(text, bold_phrases(new_marked) + paragraph.bold)

    lead = para.pieces[0]
    spans = lead.spans[para.bullet_chars:] + [s for p in para.pieces[1:] for s in p.spans]
    counts: dict[tuple, list] = {}
    for sp in spans:
        counts.setdefault((_is_bold(sp),) + _span_style(sp), []).append(sp)
    regular = [v for k, v in counts.items() if not k[0]]
    bold = [v for k, v in counts.items() if k[0]]
    regular_span = max(regular, key=len)[0] if regular else max(counts.values(), key=len)[0]
    bold_span = max(bold, key=len)[0] if bold else dict(
        regular_span, flags=regular_span["flags"] | 16, font=regular_span["font"] + "-Bold"
    )

    words, i = [], 0
    for word in text.split(" "):
        letters = [mask[i + k] for k, ch in enumerate(word) if ch.isalnum()]
        is_bold = bool(letters) and all(letters)
        span = bold_span if is_bold else regular_span
        # A hyphen drawn in an embedded font is often mapped back to a soft hyphen, and
        # "real-time" would read as "realtime". Hyphens are set in the standard font,
        # which reads back true; the letters either side keep the CV's own.
        segments = [
            (part, _fallback_font(span) if part == "-" else fonts.for_word(span, part, key))
            for part in re.split(r"(-)", word) if part
        ]
        words.append((word, is_bold, segments))
        i += len(word) + 1
    space_font = fonts.for_word(regular_span, " ", key)

    size = regular_span["size"]
    line_h = _line_height(para)
    x_first, x_next = _text_x(para, first=True), _text_x(para, first=False)
    right = para.column_right
    room = max(int((para.bottom_limit - lead.baseline) / line_h) + 1, len(para.pieces))

    for scale in (1.0, 0.97, 0.94, 0.91, 0.88):
        s = size * scale
        space = space_font.text_length(" ", fontsize=s)
        lines, line, x = [], [], 0.0
        for word, is_bold, segments in words:
            width = sum(f.text_length(part, fontsize=s) for part, f in segments)
            limit = (right - x_first) if not lines else (right - x_next)
            if line and x + space + width > limit:
                lines.append(line)
                line, x = [], 0.0
            if line:
                x += space
            line.append((x, word, is_bold, segments))
            x += width
        if line:
            lines.append(line)
        lh = line_h * scale
        if len(lines) <= room and lead.baseline + (len(lines) - 1) * lh <= para.bottom_limit + 0.5:
            return {
                "size": s, "line_h": lh, "lines": lines,
                "colors": (_rgb(regular_span["color"]), _rgb(bold_span["color"])),
                "x_first": x_first, "x_next": x_next, "baseline": lead.baseline,
            }
    return None


# A PDF content stream, read just far enough to know where each run of text sits.
_TOKEN = re.compile(
    rb"%[^\r\n]*|\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<<|>>|<[0-9A-Fa-f\s]*>"
    rb"|\[|\]|/[^\s/\[\]()<>{}%]*|[-+]?(?:\d+\.?\d*|\.\d+)|[^\s/\[\]()<>{}%]+"
)


def _mul(a, b):
    """Row-vector PDF matrix product a x b, matrices as (a, b, c, d, e, f)."""
    return (
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    )


def _inverse(m):
    det = m[0] * m[3] - m[1] * m[2]
    if abs(det) < 1e-12:
        return None
    a, b, c, d = m[3] / det, -m[1] / det, -m[2] / det, m[0] / det
    return (a, b, c, d, -(m[4] * a + m[5] * c), -(m[4] * b + m[5] * d))


def _text_blocks(stream: bytes):
    """Every BT...ET in the stream: (start, end, first glyph position, CTM at that point).

    Tracks the graphics state and text matrices the way a viewer would, only far
    enough to know where a block's first glyph lands. Inline images are skipped whole,
    since their bytes would otherwise be read as operators.
    """
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm, stack = identity, []
    blocks, operands = [], []
    in_text, start, tm, tlm, leading, first = False, 0, identity, identity, 0.0, None
    pos = 0
    while True:
        match = _TOKEN.search(stream, pos)
        if not match:
            break
        token, pos = match.group(0), match.end()
        if token.startswith(b"%"):
            continue
        head = token[:1]
        if head in b"-+.0123456789" and token not in (b"-", b"+"):
            try:
                operands.append(float(token))
                continue
            except ValueError:
                pass
        if head in (b"/", b"(", b"<", b"[", b"]") or token == b">>":
            operands.append(token)
            continue
        op = token
        nums = [o for o in operands if isinstance(o, float)]
        if op == b"BI":
            end = stream.find(b"EI", stream.find(b"ID", pos))
            pos = end + 2 if end >= 0 else len(stream)
        elif op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            ctm = stack.pop() if stack else identity
        elif op == b"cm" and len(nums) >= 6:
            ctm = _mul(tuple(nums[-6:]), ctm)
        elif op == b"BT":
            in_text, start, tm, tlm, first = True, match.start(), identity, identity, None
        elif op == b"ET" and in_text:
            in_text = False
            blocks.append((start, pos, first, ctm))
        elif in_text:
            if op == b"Tm" and len(nums) >= 6:
                tm = tlm = tuple(nums[-6:])
            elif op in (b"Td", b"TD") and len(nums) >= 2:
                if op == b"TD":
                    leading = -nums[-1]
                tlm = _mul((1, 0, 0, 1, nums[-2], nums[-1]), tlm)
                tm = tlm
            elif op == b"TL" and nums:
                leading = nums[-1]
            elif op == b"T*":
                tlm = _mul((1, 0, 0, 1, 0, -leading), tlm)
                tm = tlm
            elif op in (b"Tj", b"TJ", b"'", b'"') and first is None:
                if op in (b"'", b'"'):
                    tlm = _mul((1, 0, 0, 1, 0, -leading), tlm)
                    tm = tlm
                m = _mul(tm, ctm)
                first = (m[4], m[5])
        operands = []
    return blocks


def _splice(doc, page, main_xref: int,
            inserts: list[tuple[tuple[float, float, float], bytes]]) -> None:
    """Move newly written text into the page's content where the old text used to be.

    `inserts` are (x, baseline, right edge) of each changed paragraph, with the stream
    bytes that draw it. Each goes in straight after the run of text that sat nearest
    above it in the same column - the text the removed words used to follow - wrapped
    in the inverse of the transform in force there, so it lands where it was drawn.
    """
    stream = doc.xref_stream(main_xref)
    to_page = page.transformation_matrix
    positions = []
    for start, end, first, ctm in _text_blocks(stream):
        if first is None:
            continue
        point = __import__("pymupdf").Point(*first) * to_page
        positions.append((start, end, point.x, point.y, ctm))

    placed: list[tuple[int, bytes]] = []
    for (x, baseline, right), content in inserts:
        # Anything in this column that starts above: a heading set further left than an
        # indented bullet counts, a block in the next column across does not.
        above = [b for b in positions if b[3] < baseline - 1 and x - 40 <= b[2] <= right]
        if above:
            anchor = max(above, key=lambda b: (b[3], positions.index(b)))
            at, ctm = anchor[1], anchor[4]
        else:
            at, ctm = 0, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        inverse = _inverse(ctm)
        if inverse is None:
            continue
        prefix = ("\nq %s cm\n" % " ".join(f"{v:.6f}" for v in inverse)).encode()
        placed.append((at, prefix + content + b"\nQ\n"))

    for at, block in sorted(placed, key=lambda item: item[0], reverse=True):
        stream = stream[:at] + block + stream[at:]
    doc.update_stream(main_xref, stream)
    doc.xref_set_key(page.xref, "Contents", f"{main_xref} 0 R")


def _normal(text: str) -> str:
    """Whitespace folded, nothing else: a soft hyphen read back where a hyphen was
    written is exactly the kind of fault this check exists to catch."""
    return re.sub(r"\s+", " ", text).strip()


def _render_pdf(document: CvDocument, edits: dict[str, str], forced: set[str] | None = None) -> Rendered:
    """Write the edits into a copy of the PDF, in place and in reading order.

    The old words are removed with a redaction, which touches nothing else on the page,
    and the new ones are written in the same position, size and colour. New text is
    always added at the end of a page's content, and a parser that reads a PDF in
    stored order - several applicant tracking systems do - would then find the profile
    after the skills. So each new paragraph is moved into the stream at the point its
    old words were, and nothing that was not changed is redrawn or reordered.

    Finally the file is read back the way a parser would. A paragraph whose new words do
    not come back exactly - a font whose letters map to the wrong characters, say - is
    written again in a standard font.
    """
    import pymupdf

    forced = forced or set()
    doc = pymupdf.open(stream=document.data, filetype="pdf")
    fonts = _Fonts(doc)
    fonts.forced_fallback = forced
    applied, skipped = {}, {}

    plans: dict[str, dict] = {}
    for paragraph in document.paragraphs:
        if paragraph.id not in edits:
            continue
        new_text = edits[paragraph.id]
        plan = _plan_paragraph(paragraph, new_text, fonts, paragraph.id)
        if plan is None:
            skipped[paragraph.id] = "it would not fit in the space the original takes"
            continue
        plans[paragraph.id] = plan
        applied[paragraph.id] = new_text

    for number in sorted({document.by_id[i]._ref.page for i in plans}):
        page = doc[number]
        changed = [p for p in document.paragraphs if p.id in plans and p._ref.page == number]
        for paragraph in changed:
            para: _PdfPara = paragraph._ref
            for i, piece in enumerate(para.pieces):
                chars = piece.chars[para.bullet_chars if i == 0 else 0:]
                if not chars:
                    continue
                x0 = min(c["bbox"][0] for c in chars)
                x1 = max(c["bbox"][2] for c in chars)
                y0, y1 = piece.bbox[1], piece.bbox[3]
                inset = (y1 - y0) * 0.2
                page.add_redact_annot(pymupdf.Rect(x0 - 0.3, y0 + inset, x1 + 0.3, y1 - inset),
                                      fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        page.clean_contents()
        main_xref = page.get_contents()[0]
        base = set(page.get_contents())

        inserts = []
        for paragraph in changed:
            plan = plans[paragraph.id]
            before = set(page.get_contents())
            # Words in reading order; a new text object only where the colour changes,
            # so the order they are stored in is the order they are read in.
            writer, color = None, None
            for i, line in enumerate(plan["lines"]):
                x0 = plan["x_first"] if i == 0 else plan["x_next"]
                y = plan["baseline"] + i * plan["line_h"]
                for dx, word, is_bold, segments in line:
                    word_color = plan["colors"][is_bold]
                    if writer is None or word_color != color:
                        if writer is not None:
                            writer.write_text(page, color=color)
                        writer, color = pymupdf.TextWriter(page.rect), word_color
                    x = x0 + dx
                    for part, font in segments:
                        writer.append((x, y), part, font=font, fontsize=plan["size"])
                        x += font.text_length(part, fontsize=plan["size"])
            if writer is not None:
                writer.write_text(page, color=color)
            new = [x for x in page.get_contents() if x not in before and x not in base]
            content = b"\n".join(doc.xref_stream(x) for x in new if b"BT" in doc.xref_stream(x))
            para = paragraph._ref
            inserts.append(((plan["x_first"], plan["baseline"], para.column_right), content))
        try:
            _splice(doc, page, main_xref, inserts)
        except Exception:  # noqa: BLE001 - the text is on the page either way
            import logging

            logging.getLogger(__name__).warning("could not reorder a tailored PDF page",
                                                exc_info=True)

    out = doc.tobytes(garbage=3, deflate=True)

    # Read it back as a parser would; redo any paragraph that does not come back whole.
    readback = _normal(text_of(out, "pdf"))
    wrong = {pid for pid, text in applied.items()
             if _normal(plain(text)) not in readback and pid not in forced}
    if wrong:
        return _render_pdf(document, edits, forced | wrong)
    return Rendered(out, applied, skipped)


def text_of(data: bytes, kind: str) -> str:
    """What an applicant tracking system would read out of a finished file."""
    if kind == "pdf":
        import pymupdf

        return "\n".join(page.get_text() for page in pymupdf.open(stream=data, filetype="pdf"))
    if kind == "docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in _docx_paragraphs(document))
    return _decode(data)
