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
_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|plc|gmbh|bv|nv|sa|ag|corp|corporation|co|company|"
    r"holdings|group|international|ireland|technologies|technology)\b",
    re.IGNORECASE,
)

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
