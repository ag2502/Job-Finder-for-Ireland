"""The last pass over rewritten sentences: grammar, spelling and house consistency.

Two layers. A local lint that always runs - doubled words, stray spaces before
punctuation, a lower-case start, and ending punctuation that matches the paragraph it
replaces, so a CV whose bullets have no full stops does not suddenly grow some. Then
LanguageTool's public server, free and EU-hosted, over the changed sentences only.

Its corrections are applied only where they are safe to apply unseen: grammar, casing,
commonly confused words, and spelling of words that appear in neither the CV nor the
advert - so "PyTorch", "Snowflake" and the employer's own product names are never
"corrected". Style suggestions are ignored; the model already chose the style.
"""

from __future__ import annotations

import logging
import re

import httpx

from jobfinder.core.config import settings

logger = logging.getLogger(__name__)

_SAFE_CATEGORIES = {"GRAMMAR", "CASING", "CONFUSED_WORDS", "PUNCTUATION", "TYPOS"}
_BRITISH_WORDS = ("analyse", "organise", "optimise", "prioritise", "behaviour", "colour",
                  "centre", "programme", "modelling", "travelled", "licence", "favour")
_AMERICAN_WORDS = ("analyze", "organize", "optimize", "prioritize", "behavior", "color",
                   "center", "modeling", "traveled", "favor")


def variant(text: str) -> str:
    """Which English the CV is written in: "British" (Irish usage) or "American"."""
    lowered = text.lower()
    british = sum(lowered.count(w) for w in _BRITISH_WORDS)
    american = sum(lowered.count(w) for w in _AMERICAN_WORDS)
    return "American" if american > british else "British"


def lint(new: str, original: str) -> str:
    """Tidy one rewritten paragraph so it sits with the paragraphs around it."""
    text = re.sub(r"\s+", " ", new).strip()
    text = re.sub(r"\s+([,.;:!?%)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.I)  # "the the"
    text = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"\.\.+$", ".", text)
    if original[:1].isupper() and text[:1].islower():
        text = text[:1].upper() + text[1:]
    ends = original.rstrip()[-1:] in ".!?"
    if ends and text[-1:] not in ".!?":
        text += "."
    elif not ends and text.endswith(".") and not text.endswith(("etc.", "...")):
        text = text[:-1]
    return text


def check(paragraphs: dict[str, str], *, trusted_text: str, language: str) -> tuple[dict[str, str], list[dict]]:
    """Run the changed paragraphs past LanguageTool; return them corrected, with the fixes.

    Fails open: if the server cannot be reached the paragraphs come back unchanged, since
    the model's own care and the lint are still between the searcher and a mistake.
    """
    if not settings.languagetool_url or not paragraphs:
        return paragraphs, []
    ids = list(paragraphs)
    joined, offsets, at = "", {}, 0
    for pid in ids:
        offsets[pid] = (at, at + len(paragraphs[pid]))
        joined += paragraphs[pid] + "\n\n"
        at = len(joined)
    try:
        response = httpx.post(
            settings.languagetool_url,
            data={"text": joined, "language": "en-GB" if language == "British" else "en-US",
                  "level": "default"},
            timeout=httpx.Timeout(8.0, connect=3.0),
        )
        response.raise_for_status()
        matches = response.json().get("matches", [])
    except (httpx.HTTPError, ValueError):
        logger.info("LanguageTool unavailable; skipping the proofreading pass")
        return paragraphs, []

    trusted = trusted_text.lower()
    fixes: dict[str, list[tuple[int, int, str, str]]] = {pid: [] for pid in ids}
    for match in matches:
        category = ((match.get("rule") or {}).get("category") or {}).get("id", "")
        replacements = match.get("replacements") or []
        if category not in _SAFE_CATEGORIES or not replacements:
            continue
        start, length = match.get("offset", 0), match.get("length", 0)
        pid = next((p for p, (a, b) in offsets.items() if a <= start < b), None)
        if pid is None:
            continue
        a = offsets[pid][0]
        wrong = joined[start:start + length]
        if category == "TYPOS":
            # Technical names, employers and the advert's own terms are right by
            # definition; so is anything capitalised oddly on purpose.
            if (wrong.lower() in trusted or any(ch.isdigit() for ch in wrong)
                    or re.search(r"[a-z][A-Z]|^[A-Z]{2,}$", wrong)):
                continue
        fixes[pid].append((start - a, length, replacements[0]["value"], wrong))

    out, applied = dict(paragraphs), []
    for pid, items in fixes.items():
        text = out[pid]
        for offset, length, value, wrong in sorted(items, reverse=True):
            text = text[:offset] + value + text[offset + length:]
            applied.append({"id": pid, "from": wrong, "to": value})
        out[pid] = text
    return out, applied
