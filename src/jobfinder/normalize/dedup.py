"""Duplicate grouping.

The same role legitimately appears more than once — a company's own careers page and an
aggregator both carry it. `dedup_key` groups those together so the UI can show one entry
and prefer the company-direct source, which has the better description and the canonical
apply URL.

This key is intentionally *not* a uniqueness constraint. Companies really do post
several openings with the same title in the same city, and collapsing those would hide
real jobs. Row identity stays (source_id, source_job_id).
"""

from __future__ import annotations

import hashlib
import re

_PUNCT = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")

# Suffixes and decorations that differ between boards for the same underlying role.
# Only unambiguous legal forms belong here. Each one stripped is a pair of names that
# will now collide, so a token that doubles as an ordinary word - "spa", "as", "se" -
# would merge unrelated employers. The Irish forms matter most: "llp" and "dac" are how
# professional firms and Irish subsidiaries are registered, and their absence was
# listing ByrneWallace Shields twice, once with the suffix and once without.
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|llp|lp|ltd|limited|plc|dac|ulc|teoranta|teo|gmbh|bv|nv|sa|ag|"
    r"srl|sarl|sas|kft|pty|pvt|pte|corp|corporation|co|company|"
    r"holdings|group|international|ireland|technologies|technology)\b",
    re.IGNORECASE,
)

# Words that never end a company name on their own; see same_employer.
_CONNECTIVES = frozenset({"of", "and", "the", "for", "at", "de", "du", "van"})

_TITLE_NOISE = re.compile(
    r"\((?:remote|hybrid|onsite|on-site|contract|full[- ]time|part[- ]time|[a-z]{2,3})\)",
    re.IGNORECASE,
)


def normalize_company_name(name: str) -> str:
    text = _PUNCT.sub(" ", name.lower())
    text = _COMPANY_SUFFIXES.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def canonical_title(title: str) -> str:
    text = _TITLE_NOISE.sub(" ", title.lower())
    text = _PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def location_bucket(is_dublin: bool, is_remote: bool, location_norm: str | None) -> str:
    if is_dublin:
        return "dublin-ie"
    if is_remote:
        return "remote"
    return _WHITESPACE.sub(" ", (location_norm or "unknown").lower()).strip()


def compute_dedup_key(
    company_name: str,
    title: str,
    *,
    is_dublin: bool = False,
    is_remote: bool = False,
    location_norm: str | None = None,
) -> str:
    parts = "|".join(
        (
            normalize_company_name(company_name),
            canonical_title(title),
            location_bucket(is_dublin, is_remote, location_norm),
        )
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


def same_employer(a: str, b: str) -> bool:
    """True when two *normalized* company names plainly name one employer.

    Aggregators carry an employer's name however it was typed into them, so one firm
    arrives as "Byrne Wallace", "Byrne Wallace Shields" and "Byrne Wallace Shields LLP".
    Stripping legal suffixes settles the third; the other two differ by a word that is
    genuinely part of the name, and no amount of normalising will join them.

    A token-prefix test does, and is safe only because callers additionally require an
    identical canonical title and location. On its own it would merge any two firms
    sharing a first word.
    """
    if a == b:
        return True
    if not a or not b:
        return False

    short, long = sorted((a.split(), b.split()), key=len)
    if long[: len(short)] != short:
        return False

    # Stripping a suffix can leave a fragment that prefixes half the market: "Bank of
    # Ireland" becomes "bank of", which would swallow "Bank of America". A prefix only
    # identifies an employer when it is substantial on its own - at least two tokens,
    # and not trailing off into a connective.
    return len(short) >= 2 and short[-1] not in _CONNECTIVES
