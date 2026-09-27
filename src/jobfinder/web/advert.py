"""Lay an advert's stored text out for reading on the site.

Adapters store descriptions as plain text: markup is stripped on the way in, so what
arrives here is lines. How those lines break depends on the employer's system. Google
separates paragraphs with a blank line; Workday often breaks a sentence in two, leaving
"Contract" on one line and ": Full-time role" on the next; bullets survive as "•", "-"
or "*" at the start of a line, or not at all.

So this reads lines, not markup, and makes three conservative calls:

* A line that continues the one before (it opens with a lower-case letter or with
  punctuation) is joined back onto it.
* A short line ending in a colon, or a known section name on its own, is a heading.
* A line opening with a bullet mark is a list item.

Everything else is a paragraph. Nothing is ever dropped or reworded: the searcher is
reading the employer's own advert, and it has to say what the employer said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Section names that head a block in adverts even without a trailing colon.
_SECTIONS = re.compile(
    r"^(about (the|this) (job|role|team|position)|about (you|us)|the role|the team|"
    r"(key |main )?responsibilities|what you('| wi)ll do|what you('| wi)ll bring|"
    r"what we('| a)re looking for|what we offer|who you are|requirements|"
    r"(minimum|preferred|basic|desired|essential|required) qualifications|qualifications|"
    r"skills( and| &) experience|experience|benefits|perks|why join us|"
    r"nice to have|bonus points|your profile|your role|job description|overview|"
    r"general information|how to apply)$",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[•●▪◦‣⁃·*\-–—]|\d{1,2}[.)])\s+")
_MAX_HEADING = 70


@dataclass(frozen=True)
class Block:
    kind: str  # "h", "p" or "li"
    text: str


def _is_continuation(line: str) -> bool:
    first = line[0]
    return first.islower() or first in ",.;:)!?"


def blocks(text: str | None) -> list[Block]:
    """The advert as headings, paragraphs and list items, in order."""
    if not text or not text.strip():
        return []

    # Pass one: rebuild logical lines, rejoining the ones a system split mid-sentence.
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw.split())
        if not line:
            lines.append("")
            continue
        if lines and lines[-1] and _is_continuation(line) and not _BULLET.match(line):
            joiner = "" if line[0] in ",.;:)!?" else " "
            lines[-1] = lines[-1] + joiner + line
        else:
            lines.append(line)

    # Pass two: classify each one.
    out: list[Block] = []
    for line in lines:
        if not line:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            item = line[bullet.end():].strip()
            if item:
                out.append(Block("li", item))
            continue
        bare = line.rstrip(":").strip()
        if len(line) <= _MAX_HEADING and (
            (line.endswith(":") and len(bare.split()) <= 8) or _SECTIONS.match(bare)
        ):
            out.append(Block("h", bare))
            continue
        out.append(Block("p", line))

    # A heading with nothing after it says nothing.
    while out and out[-1].kind == "h":
        out.pop()
    return out
