"""Description text cleaning.

Most ATS platforms return descriptions as HTML. Greenhouse additionally
entity-encodes it, so an unprocessed description reaches the matcher looking like
``&lt;div class="content-intro"&gt;``. Left alone, 87% of the corpus carried markup, and
the ranker happily scored jobs on ``div``, ``span``, ``strong`` and ``br`` — tokens that
say nothing about the role but appear in almost every advert.

Cleaning happens once at reconciliation, so everything downstream (ranking, skill
extraction, corpus vocabulary) sees plain prose.
"""

from __future__ import annotations

import html
import re

from selectolax.parser import HTMLParser

_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")
_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

MAX_DESCRIPTION_CHARS = 40_000


def looks_like_html(text: str) -> bool:
    return bool(_TAG.search(text))


def html_to_text(value: str | None) -> str | None:
    """Convert an HTML (or entity-encoded HTML) description to readable plain text."""
    if not value:
        return value

    text = value

    # Greenhouse double-encodes: unescape until the markup is visible, but bounded so
    # a description containing a literal "&amp;lt;" cannot loop.
    for _ in range(3):
        if looks_like_html(text):
            break
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    if looks_like_html(text):
        try:
            tree = HTMLParser(text)
            for node in tree.css("script, style, noscript"):
                node.decompose()
            body = tree.body or tree.root
            text = body.text(separator="\n", strip=True) if body else _TAG.sub(" ", text)
        except Exception:  # noqa: BLE001 - a malformed advert must not break a crawl
            text = _TAG.sub(" ", text)

    text = html.unescape(text)
    text = text.replace("​", "").replace("\xa0", " ")
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()[:MAX_DESCRIPTION_CHARS] or None
