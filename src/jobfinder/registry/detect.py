"""ATS detection.

Given a company's website, work out which applicant tracking system hosts its jobs and
what slug addresses it. This is what lets the registry scale past a hand-maintained list:
a company name goes in, a crawlable source comes out.

Detection reads the careers page and looks for the ATS's fingerprint. The highest-value
case is a company that *embeds* a Greenhouse or Lever widget on its own domain — from the
outside it looks like a bespoke site needing a fragile scraper, but the embed reveals a
slug that turns it into a clean Tier 1 API source.

Workday is handled separately because its address is three parts (tenant, host shard and
site) rather than one slug, and no part is reliably derivable from the company name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from jobfinder.sources.base import build_client

logger = logging.getLogger(__name__)

# Ordered: the first match wins, so more specific patterns come first.
ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v\d/accounts/)?([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9_-]+)\.recruitee\.com", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9_-]+)\.teamtailor\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]

# Workday: tenant.wdNN.myworkdayjobs.com/<locale>/<Site> - the site segment is the last
# path component that is not a locale code.
WORKDAY_PATTERN = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9-]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
    re.I,
)

# Slugs that appear in boilerplate and never identify a real board.
SLUG_BLOCKLIST = {
    "embed", "js", "api", "www", "assets", "static", "images", "css",
    "board", "boards", "jobs", "job", "careers", "search", "v1", "v0",
}

CAREERS_PATHS = (
    "/careers", "/careers/", "/jobs", "/jobs/", "/en/careers",
    "/about/careers", "/company/careers", "/careers/jobs", "/join-us",
    "/work-with-us", "/opportunities",
)


@dataclass
class Detection:
    adapter: str | None = None
    slug: str | None = None
    careers_url: str | None = None
    confidence: str = "none"  # "high" | "medium" | "none"
    note: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.adapter and self.slug)


def detect_in_text(text: str) -> tuple[str, str] | None:
    """Find an ATS fingerprint in a page's markup."""
    match = WORKDAY_PATTERN.search(text)
    if match:
        tenant, host, site = match.group(1), match.group(2), match.group(3)
        if site.lower() not in SLUG_BLOCKLIST:
            return "workday", f"{tenant}:{host}:{site}"

    for adapter, pattern in ATS_PATTERNS:
        found = pattern.search(text)
        if found:
            slug = found.group(1)
            if slug.lower() in SLUG_BLOCKLIST:
                continue
            return adapter, slug
    return None


def detect_from_url(
    url: str, client: httpx.Client | None = None
) -> Detection:
    """Fetch one URL and inspect it (and its redirect chain) for an ATS."""
    owns = client is None
    client = client or build_client()
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return Detection(careers_url=url, note=f"fetch failed: {exc!r}")
    finally:
        if owns:
            client.close()

    # The final URL matters as much as the body: many careers links simply redirect
    # to the ATS, which is the strongest possible signal.
    final_url = str(response.url)
    hit = detect_in_text(final_url)
    if hit:
        return Detection(
            adapter=hit[0], slug=hit[1], careers_url=final_url, confidence="high",
            note="redirect target",
        )

    if response.status_code >= 400:
        return Detection(careers_url=url, note=f"HTTP {response.status_code}")

    hit = detect_in_text(response.text)
    if hit:
        return Detection(
            adapter=hit[0], slug=hit[1], careers_url=final_url, confidence="high",
            note="embedded in page",
        )

    return Detection(careers_url=final_url, note="no ATS fingerprint found")


def detect_for_website(
    website: str, client: httpx.Client | None = None
) -> Detection:
    """Try a company's likely careers paths until an ATS is found."""
    website = website.rstrip("/")
    if not website.startswith("http"):
        website = f"https://{website}"

    owns = client is None
    client = client or build_client()
    try:
        for path in CAREERS_PATHS:
            result = detect_from_url(f"{website}{path}", client=client)
            if result.found:
                return result
        # Fall back to the homepage, which often links to the board.
        return detect_from_url(website, client=client)
    finally:
        if owns:
            client.close()
