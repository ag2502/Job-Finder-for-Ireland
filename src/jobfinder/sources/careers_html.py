"""Generic extraction from a careers page's own HTML.

`jsonld` reaches every employer that publishes `schema.org/JobPosting` markup. This
adapter is for the ones that do not: a council, a law firm or a hotel group whose
careers page is a plain list of vacancies, each linking to a page describing the role.
Those were the bulk of the `BLOCKED` queue, and no fingerprint or structured-data
convention will ever reach them.

## How a job list is recognised

There is no markup to rely on, so the extractor looks for the shape every vacancy list
shares: several links, side by side, that point at *sibling* pages — the same path with
a different id or title slug at the end — and whose anchor text reads like a job title
rather than navigation. A menu has siblings too (`/careers/benefits`,
`/careers/culture`), which is why a group also needs its URLs to carry an identifier, or
its text to look like a role, before it counts.

Most careers pages are brochures, and the list is one click further on, behind "Current
vacancies" or "View open roles". The careers page is read first; if it holds no list,
the links that say where the list is are followed, one hop only.

## Why every candidate is opened before it counts

Link shape alone produces false positives that look entirely plausible — The Irish
Times lists "Jobs in Co. Antrim" through "Jobs in Co. Wexford" as thirty-two tidy
siblings. So each candidate page is fetched, and it is a job only if it carries
`JobPosting` markup or offers a way to apply. A category page offers neither.

Politeness and the crawl ceilings follow `jsonld`: robots.txt is honoured per origin,
and a list cut short by the page cap or by pagination is reported as partial, so the
reconciler never closes the roles it did not get to read.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx
from selectolax.parser import HTMLParser

from jobfinder.normalize.text import html_to_text
from jobfinder.sources.policy import ExcludedSite, is_excluded
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import (
    MAX_JOB_PAGES,
    RobotsPolicy,
    _is_job_posting,
    iter_ld_objects,
    parse_job_posting,
    title_from_url,
)

logger = logging.getLogger(__name__)

# Links that say "the vacancy list is over here". Matched against anchor text and href.
LISTING_WORDS = re.compile(
    r"vacanc|open[-_ ]?(roles|positions|jobs)|current[-_ ]?(opportunit|openings|roles|jobs)"
    r"|job[-_ ]?(search|openings|board|listings)|search[-_ ]?(all[-_ ]?)?(jobs|roles)"
    r"|find[-_ ]?a[-_ ]?job|(see|view|explore|browse)[-_ ]?(all[-_ ]?|open[-_ ]?)?(jobs|roles|opportunit)"
    r"|career[-_ ]?opportunit|all[-_ ]?jobs|job[-_ ]?opportunit|opportunities",
    re.I,
)
MAX_LISTING_HOPS = 3

# Anchor texts that are navigation or calls to action, never a role's title.
NAV_TEXT = re.compile(
    r"^(learn|read|find out|see|view|show|discover|explore)( more| details| all| job| role| now)?\b"
    r"|^(apply|apply now|apply here|more|more info|details|next|previous|back|home|menu|search)$"
    r"|^(careers?|jobs?|vacancies|current vacancies|opportunities|overview|benefits|culture"
    r"|values|our people|about( us)?|contact( us)?|news|blog|events|faqs?|login|log in|sign in"
    r"|register|graduates?|students?|internships?|apprenticeships?|early careers|privacy"
    r"|cookies?|saved jobs.*|your jobs)$"
    r"|^(life at|why |meet |jobs in |working at |work at )|^\d+$",
    re.I,
)
# Link texts that still name the job but only as a call to action; the title then comes
# from the job page itself.
GENERIC_TEXT = re.compile(
    r"^(apply( now| here)?\.?|see details|view (job|role|details)|more (info|details)|read more"
    r"|learn more|click here|find out more)$",
    re.I,
)

# Words that make an anchor text read as a role.
ROLE_WORDS = re.compile(
    r"\b(manager|engineer|developer|analyst|officer|assistant|associate|director|lead"
    r"|specialist|consultant|executive|administrator|coordinator|co-ordinator|technician"
    r"|nurse|clerk|accountant|advisor|adviser|architect|designer|scientist|researcher"
    r"|lecturer|professor|head of|supervisor|operative|driver|chef|intern|graduate"
    r"|representative|agent|controller|auditor|solicitor|lawyer|paralegal|therapist"
    r"|pharmacist|doctor|registrar|porter|attendant|planner|surveyor|inspector|trainee"
    r"|apprentice|president|worker|tester|recruiter)\b",
    re.I,
)

# A URL that addresses one posting: an id in the path or the query.
ID_SEGMENT = re.compile(r"\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}", re.I)
ID_PARAMS = re.compile(r"^(job|jid|jobid|job_id|id|vacancy|vacancyid|req|reqid|posting|p|ref|position|opening)$", re.I)

APPLY_SIGNAL = re.compile(r"\bapply\b|\bapplication form\b|\bcloses?\b|\bclosing date\b", re.I)
PAGINATION = re.compile(r"^(next|›|»|next page|>)$|^\d+$", re.I)
LOCATION_LABEL = re.compile(
    r"\b(?:location|locations|based in|work location|office|county)\b\s*(?:[:\-–]\s*|\n\s*)"
    r"([^\n|•]{2,80})",
    re.I,
)
# A Dublin mention near the top of an advert, where the role's own details sit. Read no
# further: a page footer listing every office would make every role look like Dublin.
DUBLIN_NEAR_TOP = re.compile(r"\b(Dublin(?: \d{1,2})?)\b")
TOP_OF_ADVERT = 1500

SKIP_EXTENSIONS = (".pdf", ".doc", ".docx", ".jpg", ".png", ".zip")
MIN_GROUP = 2


class Link:
    __slots__ = ("url", "text", "card")

    def __init__(self, url: str, text: str, card: str) -> None:
        self.url = url
        self.text = text
        self.card = card


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def page_links(html: str, base_url: str) -> list[Link]:
    """Every outbound link with its text and the text of the block around it."""
    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript, header, footer, nav"):
        node.decompose()

    links: list[Link] = []
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith(("http://", "https://")):
            continue
        if urlsplit(url).path.lower().endswith(SKIP_EXTENSIONS):
            continue
        text = _clean(anchor.text(separator=" ") or anchor.attributes.get("title") or "")
        parent = anchor.parent
        card = _clean(parent.text(separator=" | ")) if parent is not None else text
        links.append(Link(url, text, card[:400]))
    return links


def _group_key(url: str) -> tuple[str, str, str]:
    """Links that differ only in their final identifier share a key."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    parent = "/".join(ID_SEGMENT.sub("#", s) for s in segments[:-1])
    params = ",".join(sorted(k for k, _ in parse_qsl(parts.query) if ID_PARAMS.match(k)))
    return parts.netloc.lower(), parent, params


def _addresses_one_posting(url: str) -> bool:
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    last = segments[-1] if segments else ""
    if ID_SEGMENT.search(last):
        return True
    return any(ID_PARAMS.match(k) and v for k, v in parse_qsl(parts.query))


def _reads_as_role(text: str) -> bool:
    return bool(ROLE_WORDS.search(text)) and 4 <= len(text) <= 150


def job_candidates(links: list[Link], page_url: str) -> list[Link]:
    """The links on a page that form a vacancy list, best evidence first."""
    groups: dict[tuple[str, str, str], list[Link]] = defaultdict(list)
    seen: set[str] = set()
    page = page_url.split("#")[0].rstrip("/")

    for link in links:
        if link.url.rstrip("/") == page or link.url in seen:
            continue
        if link.text and NAV_TEXT.search(link.text) and not GENERIC_TEXT.match(link.text):
            continue
        seen.add(link.url)
        groups[_group_key(link.url)].append(link)

    chosen: list[Link] = []
    for members in groups.values():
        if len(members) < MIN_GROUP:
            continue
        addressed = sum(_addresses_one_posting(m.url) for m in members)
        roles = sum(_reads_as_role(m.text) for m in members)
        # A group earns its place with titles that read as roles, or with a posting id
        # in every URL. A slug alone proves nothing: `/careers/making-applications` is
        # as descriptive as `/careers/staff-nurse`, and advice pages come in groups too.
        if roles * 2 >= len(members) or addressed == len(members):
            chosen.extend(members)
    return chosen


def listing_links(links: list[Link]) -> list[str]:
    """Links that point at the vacancy list, most explicit first."""
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in links:
        if link.url in seen:
            continue
        text_hit = bool(LISTING_WORDS.search(link.text))
        href_hit = bool(LISTING_WORDS.search(urlsplit(link.url).path))
        if not (text_hit or href_hit):
            continue
        seen.add(link.url)
        scored.append(((2 if text_hit else 0) + (1 if href_hit else 0), link.url))
    scored.sort(key=lambda pair: -pair[0])
    return [url for _, url in scored]


def _has_pagination(links: list[Link]) -> bool:
    return any(
        PAGINATION.match(link.text) and ("page" in link.url.lower() or "p=" in link.url.lower())
        for link in links
    )


def _location_from(text: str) -> str | None:
    for match in LOCATION_LABEL.finditer(text):
        value = _clean(match.group(1)).strip(" ,.|:")
        # A label followed by a button or a heading ("Location" then "Apply") is layout,
        # not a place.
        if value and not NAV_TEXT.search(value) and not GENERIC_TEXT.match(value):
            return value
    return None


def parse_job_page(html: str, url: str, link: Link) -> RawJob | None:
    """A posting from its own page, or None if the page is not a job."""
    for obj in iter_ld_objects(html):
        if _is_job_posting(obj):
            job = parse_job_posting(obj, url)
            if job:
                if not job.location_raw:
                    in_title = DUBLIN_NEAR_TOP.search(job.title)
                    job.location_raw = f"{in_title.group(1)}, Ireland" if in_title else None
                return job

    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript, header, footer, nav"):
        node.decompose()
    body = tree.css_first("main") or tree.css_first("article") or tree.body
    text = body.text(separator="\n", strip=True) if body is not None else ""
    if not APPLY_SIGNAL.search(text):
        return None

    title = link.text if link.text and not GENERIC_TEXT.match(link.text) else None
    if not title:
        heading = tree.css_first("h1")
        title = _clean(heading.text()) if heading is not None else title_from_url(url)
    # Without markup, the title is the last line of defence against a news story or a
    # "how to apply" page that happens to mention applying.
    if not title or not _reads_as_role(title):
        return None

    location = _location_from(link.card) or _location_from(text)
    if not location:
        near_top = DUBLIN_NEAR_TOP.search(f"{title}\n{text[:TOP_OF_ADVERT]}")
        location = f"{near_top.group(1)}, Ireland" if near_top else None

    description = html_to_text(body.html) if body is not None else None
    return RawJob(
        source_job_id=url,
        title=title[:300],
        url=url,
        location_raw=location,
        description=description,
    )


class CareersHtmlAdapter(BaseAdapter):
    """Read a vacancy list straight from a careers page's HTML.

    The slug is the page holding the list. Registration stores the page found during the
    trial so a crawl goes straight to it; if the site has since moved the list, the same
    one-hop search runs again rather than failing.
    """

    name = "careers_html"
    tier = 3

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        start = slug if "//" in slug else f"https://{slug}"
        robots_cache: dict[str, RobotsPolicy] = {}
        listing, candidates, truncated = self.find_listing(start, client, robots_cache)
        if not candidates:
            raise ValueError(f"no vacancy list found at {start}")

        robots = _robots(listing, client, robots_cache)
        jobs: dict[str, RawJob] = {}
        for link in candidates[:MAX_JOB_PAGES]:
            if is_excluded(link.url) or not robots.allows(link.url):
                continue
            try:
                response = client.get(link.url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.debug("careers_html: %s unreachable: %r", link.url, exc)
                continue
            job = parse_job_page(response.text, str(response.url), link)
            if job:
                jobs.setdefault(job.source_job_id, job)
            self.polite_pause()

        if not jobs:
            raise ValueError(f"no job pages confirmed under {listing}")
        if truncated or len(candidates) > MAX_JOB_PAGES:
            return PartialJobs(jobs.values())
        return list(jobs.values())

    def find_listing(
        self,
        start: str,
        client: httpx.Client,
        robots_cache: dict[str, RobotsPolicy] | None = None,
    ) -> tuple[str, list[Link], bool]:
        """(listing URL, job links on it, whether the list is paginated)."""
        if is_excluded(start):
            raise ExcludedSite(f"{start} is on a site this project does not crawl")
        cache = robots_cache if robots_cache is not None else {}
        robots = _robots(start, client, cache)
        if not robots.allows(start):
            raise PermissionError(f"robots.txt disallows {start}")

        response = client.get(start)
        response.raise_for_status()
        page_url = str(response.url)
        links = page_links(response.text, page_url)
        found = job_candidates(links, page_url)
        if found:
            return page_url, found, _has_pagination(links)

        for hop in [url for url in listing_links(links) if not is_excluded(url)][:MAX_LISTING_HOPS]:
            hop_robots = _robots(hop, client, cache)
            if not hop_robots.allows(hop):
                continue
            try:
                hop_response = client.get(hop)
                hop_response.raise_for_status()
            except httpx.HTTPError:
                continue
            hop_url = str(hop_response.url)
            hop_links = page_links(hop_response.text, hop_url)
            found = job_candidates(hop_links, hop_url)
            if found:
                return hop_url, found, _has_pagination(hop_links)
            self.polite_pause()

        return page_url, [], False

def _robots(url: str, client: httpx.Client, cache: dict[str, RobotsPolicy]) -> RobotsPolicy:
    """robots.txt per origin, read once per crawl: the list and its job pages can sit on
    different hosts, and each host's own rules apply."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in cache:
        cache[origin] = RobotsPolicy(origin, client)
    return cache[origin]


register(CareersHtmlAdapter())
