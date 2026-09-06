"""Generic extraction from `schema.org/JobPosting` markup.

Every other adapter here targets one platform. This one targets a *convention*, and
that is what makes it the widest single source of coverage in the project: Google
requires `JobPosting` structured data for a role to appear in Google's jobs results, so
a large share of employers publish machine-readable job data regardless of what software
renders their careers site. One extractor therefore reaches employers on Teamtailor,
BambooHR, JazzHR, Pinpoint, Occupop, bespoke WordPress careers pages and everything else
that no adapter will ever be written for.

This is the `GENERIC_EXTRACTION` coverage state, and it is what companies fall back to
after ATS detection returns `BLOCKED`.

## Why it is a crawler and not a request

The structured data is on individual *job* pages, not on the careers *listing* page —
a listing page typically carries only `Organization` markup. So extraction is two-stage:
discover the job URLs, then read each one.

Discovery prefers `sitemap.xml`, because a sitemap is an explicit, cheap, complete
statement of what a site publishes, and falls back to scraping links off the careers
page when there is no usable sitemap. Both are capped: an uncapped crawler pointed at a
few thousand unknown domains is a liability, not a feature.

## Politeness

Unlike the ATS adapters, this one visits sites that never invited it. It therefore
honours `robots.txt` — the API-backed adapters call documented public endpoints, but a
generic crawler reading arbitrary careers pages is exactly the client `robots.txt`
exists to govern. A disallowed path is skipped, and a site that disallows the crawler
entirely yields nothing rather than being crawled anyway.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from dateutil import parser as date_parser

from jobfinder.core.config import settings
from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

LD_BLOCK = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
ANCHOR_HREF = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"']", re.I)
SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)

# URL shapes that look like an individual posting rather than a listing or a category.
# The short segments matter as much as the obvious words: Recruitee posts under `/o/`
# and Workable under `/j/`, so a word-only heuristic silently finds nothing on exactly
# the sites whose markup is cleanest.
JOB_URL_HINT = re.compile(
    r"/(job|jobs|career|careers|vacancy|vacancies|vacature|position|positions|"
    r"opening|openings|opportunity|opportunities|role|roles|stelle|stellenangebote)[/\-_]"
    r"|/(o|j|p)/[a-z0-9]",
    re.I,
)

# Ceilings. A generic crawler aimed at thousands of unknown domains needs a hard stop
# that does not depend on the site behaving reasonably.
MAX_JOB_PAGES = 60
MAX_SITEMAP_FETCHES = 5


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None


def iter_ld_objects(html: str):
    """Yield every JSON-LD object in a page, flattening `@graph` and arrays."""
    for block in LD_BLOCK.findall(html):
        try:
            payload = json.loads(block.strip())
        except (ValueError, TypeError):
            continue

        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                yield item


def _is_job_posting(obj: dict) -> bool:
    types = obj.get("@type")
    if isinstance(types, str):
        return types.lower() == "jobposting"
    if isinstance(types, list):
        return any(isinstance(t, str) and t.lower() == "jobposting" for t in types)
    return False


def _text(value) -> str | None:
    """Coerce a schema.org value that may be a string, number, list, or nested object.

    Numbers must be handled explicitly. Publishers commonly emit an integer job id
    (`"value": 2727516`), and silently returning None for it sends `identifier` down its
    fallback path — which is the *company* name, identical for every posting on the
    site. Every job on a board then shares one source id and the crawl stores exactly
    one of them.
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for entry in value:
            text = _text(entry)
            if text:
                return text
        return None
    if isinstance(value, dict):
        return _text(value.get("value") or value.get("name") or value.get("@value"))
    return None


def title_from_url(url: str) -> str | None:
    """Recover a title from a posting's URL slug.

    Some publishers emit `"title": ""` and put the real title only in the page body.
    The slug is already a hyphenated form of it, so a role is recovered rather than
    dropped — and a job with an approximate title beats a job that is missing.
    """
    path = urlsplit(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    # Trailing numeric disambiguators ("-3", "-1") are noise, not part of the title.
    slug = re.sub(r"-\d+$", "", slug)
    words = [word for word in re.split(r"[-_]+", slug) if word and not word.isdigit()]
    if not words:
        return None
    return " ".join(word.capitalize() for word in words)


def format_address(place) -> str | None:
    """Render a schema.org `Place`/`PostalAddress` as a raw location string."""
    if isinstance(place, list):
        for entry in place:
            formatted = format_address(entry)
            if formatted:
                return formatted
        return None
    if not isinstance(place, dict):
        return _text(place)

    address = place.get("address")
    if isinstance(address, (dict, list)):
        place = address if isinstance(address, dict) else (address[0] if address else {})
    elif isinstance(address, str):
        return address.strip() or None

    if not isinstance(place, dict):
        return None

    country = place.get("addressCountry")
    parts = [
        _text(place.get("addressLocality")),
        _text(place.get("addressRegion")),
        _text(country),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


def job_locations(obj: dict) -> tuple[str | None, list[str]]:
    """Primary and additional locations for a posting.

    `jobLocation` may be a single Place or a list of them, and a list here is genuinely
    per-posting — the same semantics as Ashby's `secondaryLocations`, not Greenhouse's
    company-wide `offices` — so the extras are expanded.
    """
    raw = obj.get("jobLocation")
    entries = raw if isinstance(raw, list) else [raw]

    formatted = [format_address(entry) for entry in entries]
    locations = [f for f in formatted if f]

    primary = locations[0] if locations else None
    extras = [loc for loc in locations[1:] if loc != primary]

    if obj.get("jobLocationType") == "TELECOMMUTE" and not primary:
        primary = "Remote"

    return primary, extras


def parse_job_posting(obj: dict, page_url: str) -> RawJob | None:
    """Turn one `JobPosting` object into a RawJob, or None if it is unusable."""
    url = _text(obj.get("url")) or page_url

    title = _text(obj.get("title")) or _text(obj.get("name")) or title_from_url(url)
    if not title:
        return None

    # `identifier` is often a nested PropertyValue whose `value` is the job id and whose
    # `name` is the *company* — so only `value` is read here. Falling back to `name`
    # would give every posting on a board the same id. The URL is the safe fallback:
    # unique per posting, and stable across crawls, which is what the reconciler needs.
    identifier = obj.get("identifier")
    if isinstance(identifier, dict):
        source_id = _text(identifier.get("value"))
    else:
        source_id = _text(identifier)
    source_id = source_id or url

    primary, extras = job_locations(obj)

    return RawJob(
        source_job_id=str(source_id),
        title=title,
        url=url,
        location_raw=primary,
        description=_text(obj.get("description")),
        posted_at=_parse_date(obj.get("datePosted")),
        department=_text(obj.get("occupationalCategory")) or _text(obj.get("industry")),
        extra_locations=extras,
    )


class RobotsPolicy:
    """`robots.txt` for one origin, fetched once and reused."""

    def __init__(self, origin: str, client: httpx.Client) -> None:
        self._parser = RobotFileParser()
        self._ok = True
        try:
            response = client.get(f"{origin}/robots.txt")
            if response.status_code == 200:
                self._parser.parse(response.text.splitlines())
            else:
                # No robots.txt means no restrictions.
                self._parser.parse([])
        except httpx.HTTPError:
            # An unreachable robots.txt is not permission. Treating a fetch failure as
            # "allowed" would crawl exactly the sites least able to serve the request.
            self._ok = False

    def allows(self, url: str) -> bool:
        if not self._ok:
            return False
        return self._parser.can_fetch(settings.user_agent, url)

    def sitemaps(self) -> list[str]:
        """Sitemaps the site declares in robots.txt.

        Worth reading rather than guessing `/sitemap.xml`: plenty of sites publish
        theirs somewhere else entirely — gradireland's is at
        `/sitemap/sitemap-index.xml` — and the declared location is authoritative where
        a guess is not.
        """
        if not self._ok:
            return []
        return list(self._parser.site_maps() or [])


class JsonLdAdapter(BaseAdapter):
    """Extract postings from any careers site publishing JobPosting structured data.

    The slug is the careers page URL rather than a platform identifier, because there is
    no platform — the only address a generic extractor has is the page itself.
    """

    name = "jsonld"
    tier = 3

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        careers_url = slug if "//" in slug else f"https://{slug}"
        origin = _origin(careers_url)

        robots = RobotsPolicy(origin, client)
        if not robots.allows(careers_url):
            raise PermissionError(f"robots.txt disallows {careers_url}")

        candidates = self._discover(careers_url, origin, client, robots)
        if not candidates:
            raise ValueError(f"no job pages discovered under {careers_url}")

        jobs: dict[str, RawJob] = {}
        for url in candidates[:MAX_JOB_PAGES]:
            for job in self._extract_page(url, client):
                # A posting can appear under several URLs (a listing card and its own
                # page). Keyed by source id so the same role is stored once.
                jobs.setdefault(job.source_job_id, job)
            self.polite_pause()

        if not jobs:
            raise ValueError(f"no JobPosting markup found under {careers_url}")
        return list(jobs.values())

    def _extract_page(self, url: str, client: httpx.Client) -> list[RawJob]:
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("jsonld: %s unreachable: %r", url, exc)
            return []

        found: list[RawJob] = []
        for obj in iter_ld_objects(response.text):
            if not _is_job_posting(obj):
                continue
            job = parse_job_posting(obj, str(response.url))
            if job:
                found.append(job)
        return found

    def _discover(
        self,
        careers_url: str,
        origin: str,
        client: httpx.Client,
        robots: RobotsPolicy,
    ) -> list[str]:
        """Job page URLs, from the sitemap if there is one and the page if not."""
        urls = self._from_sitemap(origin, client, robots)
        if urls:
            logger.debug("jsonld: %d job URLs from sitemap for %s", len(urls), origin)
            return urls

        urls = self._from_listing(careers_url, client, robots)
        logger.debug("jsonld: %d job URLs from listing for %s", len(urls), careers_url)

        # The careers page itself sometimes carries the postings inline, so it is always
        # worth reading even when it yielded no links.
        return [careers_url] + urls

    def _from_sitemap(
        self, origin: str, client: httpx.Client, robots: RobotsPolicy
    ) -> list[str]:
        """Job URLs listed in the site's sitemap.

        Sitemap indexes point at further sitemaps; those whose own URL mentions jobs are
        followed first, since a large site's job sitemap is the only one worth fetching.
        """
        found: list[str] = []
        # Declared sitemaps first; the conventional locations are only a fallback.
        queue = robots.sitemaps() + [
            f"{origin}/sitemap.xml",
            f"{origin}/sitemap_index.xml",
        ]
        fetched = 0

        while queue and fetched < MAX_SITEMAP_FETCHES:
            url = queue.pop(0)
            if not robots.allows(url):
                continue
            try:
                response = client.get(url)
                if response.status_code != 200:
                    continue
            except httpx.HTTPError:
                continue
            fetched += 1

            locations = SITEMAP_LOC.findall(response.text)
            nested = [loc for loc in locations if loc.endswith((".xml", ".xml.gz"))]
            pages = [loc for loc in locations if loc not in nested]

            found.extend(loc for loc in pages if JOB_URL_HINT.search(loc))
            # Job-related sitemaps first; the rest are unlikely to repay a fetch.
            queue.extend(sorted(nested, key=lambda loc: not JOB_URL_HINT.search(loc)))

            if len(found) >= MAX_JOB_PAGES:
                break

        return _dedupe(found)

    def _from_listing(
        self, careers_url: str, client: httpx.Client, robots: RobotsPolicy
    ) -> list[str]:
        """Job URLs linked from the careers page.

        Only the one page is read. Paginated listings are not followed: the sitemap path
        above already covers large boards completely, and a site with no sitemap and
        dozens of listing pages is not worth a speculative multi-page crawl.
        """
        found: list[str] = []
        base_host = urlsplit(careers_url).netloc

        try:
            response = client.get(careers_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return found

        for href in ANCHOR_HREF.findall(response.text):
            absolute = urljoin(str(response.url), href.strip())
            if not absolute.startswith(("http://", "https://")):
                continue
            # Staying on-site keeps a "powered by" footer link from turning into a
            # crawl of the vendor's marketing site.
            if urlsplit(absolute).netloc != base_host:
                continue
            if not JOB_URL_HINT.search(absolute):
                continue
            if not robots.allows(absolute):
                continue
            found.append(absolute)

        return _dedupe(found)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


register(JsonLdAdapter())
