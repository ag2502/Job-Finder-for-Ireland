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

## Finding the careers page without guessing

The first version of this module tried twelve guessed paths (`/careers`, `/jobs`, …) in
order, then the homepage. That is up to thirteen requests per company, and across a
registry of thousands it is the difference between a detection sweep that finishes and
one that does not.

The order is now inverted: fetch the homepage once, and read the careers link out of its
own navigation. Sites know where their careers page is and link to it, so following that
link is both cheaper and more accurate than guessing — it finds `/en-ie/work-with-us`
and `careers.example.ie`, which no fixed list of paths would ever contain. Guessed paths
remain as a fallback for the minority of homepages that link to careers only from a
JavaScript-rendered menu.

A company whose careers page is found but whose ATS is not is still a useful result:
`careers_url` is what the portal links to for companies that cannot be crawled, so that
they appear in the directory rather than silently missing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from jobfinder.core.config import settings
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
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9_-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9_-]+)\.teamtailor\.com", re.I)),

    # Platforms recognised but not yet crawlable. Naming them is the point: a company on
    # Oracle Recruiting Cloud is a known quantity with a known cost to support, whereas
    # the same company recorded as an anonymous "blocked" is indistinguishable from a
    # site with no careers page at all. `bulk_detect` records these and declines to
    # register a source, so `jobfinder coverage` reports which missing adapter would buy
    # the most coverage rather than leaving it to guesswork.
    ("oracle_recruiting", re.compile(r"([a-z0-9_-]+)\.fa\.[a-z0-9]+\.oraclecloud\.com/hcmUI/CandidateExperience", re.I)),
    ("successfactors", re.compile(r"career\d*\.successfactors\.(?:eu|com)/career\?company=([a-zA-Z0-9_-]+)", re.I)),
    ("taleo", re.compile(r"([a-z0-9_-]+)\.taleo\.net", re.I)),
    ("eightfold", re.compile(r"([a-z0-9_-]+)\.eightfold\.ai", re.I)),
    ("icims", re.compile(r"([a-z0-9_-]+)\.icims\.com", re.I)),
    ("avature", re.compile(r"([a-z0-9_-]+)\.avature\.net", re.I)),

    # Found by scanning the careers pages left BLOCKED: each of these is where a real
    # company's "view vacancies" link actually goes. Most are small or Irish systems that
    # no fingerprint list had named, so those companies looked like sites with nothing
    # to read.
    ("bamboohr", re.compile(r"([a-z0-9_-]+)\.bamboohr\.com", re.I)),
    ("hirehive", re.compile(r"([a-z0-9_-]+)\.hirehive\.com", re.I)),
    ("occupop", re.compile(r"([a-z0-9_-]+)\.occupop-careers\.com", re.I)),
    ("breezy", re.compile(r"([a-z0-9_-]+)\.breezy\.hr", re.I)),
    ("pinpoint", re.compile(r"([a-z0-9_-]+)\.pinpointhq\.com", re.I)),
    ("jazzhr", re.compile(r"([a-z0-9_-]+)\.applytojob\.com", re.I)),
    ("comeet", re.compile(r"comeet\.(?:com|co)/jobs/([a-z0-9_-]+)", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([a-z0-9_-]+)", re.I)),
    ("dayforce", re.compile(r"jobs\.dayforcehcm\.com/(?:[a-z]{2}-[a-z]{2}/)?([a-z0-9_-]+)", re.I)),
    ("ukg", re.compile(r"recruiting2?\.ultipro\.com/([a-z0-9]+)", re.I)),
    ("adp", re.compile(r"workforcenow\.adp\.com/[^\"'\s<>]*?[?&](?:amp;)?cid=([0-9a-f-]{36})", re.I)),
    ("cornerstone", re.compile(r"([a-z0-9_-]+)\.csod\.com", re.I)),
    ("peoplehr", re.compile(r"([a-z0-9_-]+)\.peoplehr\.net", re.I)),
    ("njoyn", re.compile(r"([a-z0-9_-]+)\.njoyn\.com", re.I)),
    ("keyhire", re.compile(r"([a-z0-9_-]+)\.keyhire\.ie", re.I)),
    ("peoplefirst", re.compile(r"([a-z0-9_-]+)\.jobs\.people-first\.com", re.I)),
    ("reach_ats", re.compile(r"([a-z0-9_-]+)\.reach-ats\.com", re.I)),
]

# Workday: tenant.wdNN.myworkdayjobs.com/<locale>/<Site> - the site segment is the last
# path component that is not a locale code.
WORKDAY_PATTERN = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9-]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
    re.I,
)

# Path segments after a Workday tenant host that are app routes, not site names.
WORKDAY_NON_SITES = {"wday", "login", "userhome", "cxs", "static", "assets"}

# Slugs that appear in boilerplate and never identify a real board.
SLUG_BLOCKLIST = {
    "embed", "js", "api", "www", "assets", "static", "images", "css",
    "board", "boards", "jobs", "job", "careers", "search", "v1", "v0",
    "help", "support", "docs", "blog", "app", "cdn", "media", "static2",
    # Vendor infrastructure hosts: `cdn1.hirehive.com` serves a widget script and
    # `clients.njoyn.com` is Njoyn's shared login, neither of them a customer's board.
    "cdn1", "cdn2", "clients", "login", "secure",
}

TEST_TENANT_SUFFIX = re.compile(r"-(sandbox|demo|staging|test)$", re.I)

# Personio's slug pattern also matches its own marketing domain.
HOST_BLOCKLIST = {
    "personio", "recruitee", "teamtailor", "greenhouse", "lever", "ashby",
    "hirehive", "breezy", "pinpoint", "occupop", "workwithus",
}

CAREERS_PATHS = (
    "/careers", "/careers/", "/jobs", "/jobs/", "/en/careers",
    "/about/careers", "/company/careers", "/careers/jobs", "/join-us",
    "/work-with-us", "/opportunities",
)

# Words that mark a link as leading to a careers page. Matched against both the href
# and the anchor text, because plenty of sites link "Join the team" to `/life`.
CAREERS_WORDS = re.compile(
    r"career|careers|jobs|vacanc|vacature|join[-_ ]?us|work[-_ ]?(with|for|at)[-_ ]?us"
    r"|work[-_ ]?here|opportunit|recruit|hiring|life[-_ ]?at|we[-_ ]?re[-_ ]?hiring"
    r"|employment|open[-_ ]?roles|open[-_ ]?positions",
    re.I,
)

# Off-site hosts a "careers" link can point at without being a careers page. Off-site
# links score highest in `careers_links`, because an off-site careers link is usually the
# ATS — which made these the *top* pick whenever a footer carried them: CPL's careers URL
# was recorded as its YouTube channel, Morgan McKinley's as its Facebook page, and
# Phorest's as Teamtailor's "powered by" marketing page. Vendor entries are the marketing
# apex only; a vendor's board hosts (`boards.greenhouse.io`, `apply.workable.com`) are
# exactly the links detection wants and stay allowed.
NON_CAREERS_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "threads.net", "pinterest.com",
    "teamtailor.com", "greenhouse.io", "greenhouse.com", "lever.co", "workable.com",
    "bamboohr.com", "personio.com", "personio.de", "ashbyhq.com",
    "smartrecruiters.com", "recruitee.com", "workday.com",
    "hirehive.com", "occupop.com", "breezy.hr", "pinpointhq.com", "jazzhr.com",
}
NON_CAREERS_HOST_FRAGMENTS = ("glassdoor.", "indeed.")


def _is_non_careers_host(host: str) -> bool:
    host = host.lower().split(":")[0].removeprefix("www.")
    if host in NON_CAREERS_HOSTS:
        return True
    if any(host.endswith("." + social) for social in (
        "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
        "youtube.com", "tiktok.com",
    )):
        return True
    return any(fragment in host for fragment in NON_CAREERS_HOST_FRAGMENTS)


ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAGS = re.compile(r"<[^>]+>")

# How many discovered careers links to follow before giving up. Beyond a handful the
# marginal hit rate collapses and the cost per company does not.
MAX_CAREERS_LINKS = 3
MAX_GUESSED_PATHS = 4

# Links followed from a careers page towards the board itself. Kept tight because this
# multiplies: MAX_CAREERS_LINKS x MAX_SECOND_HOP_LINKS requests in the worst case.
MAX_SECOND_HOP_LINKS = 2

# Subdomains large employers put their board on. `careers.example.com` is invisible to
# link discovery whenever the homepage menu is rendered client-side, which is most of
# them.
CAREERS_SUBDOMAINS = ("careers", "jobs")


class Budget:
    """A wall-clock ceiling for detecting one company.

    Detection makes up to about twenty requests per company across the open internet,
    where a site can accept a connection and then simply never finish responding. A
    per-request timeout does not bound that, because the next request starts a fresh
    one; only a whole-company deadline does. Without it a single unresponsive host holds
    a worker indefinitely and a sweep of thousands stops making progress — which is
    exactly how the first full sweep of this registry stalled.

    Running out of budget is a normal outcome, not an error: whatever has been found so
    far is returned, and the careers URL alone is still worth recording.
    """

    def __init__(self, seconds: float | None = None) -> None:
        self._deadline = time.monotonic() + (
            seconds if seconds is not None else settings.detect_budget_seconds
        )

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._deadline

    @property
    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())


def fetch_capped(client: httpx.Client, url: str) -> httpx.Response:
    """GET a URL with detection's shorter timeout, a body-size cap, and a read deadline.

    Detection only ever reads markup near the top of a page, so the response is truncated
    rather than streamed in full. Three separate limits are needed, and each covers a
    failure the others do not:

    * **Size** stops a site serving a huge file from exhausting memory.
    * **Timeout** (httpx's) bounds the connect and each individual read.
    * **Deadline** bounds the read *loop*. This is the one that is easy to omit and the
      reason the first two sweeps of this registry stalled: httpx's read timeout applies
      per chunk, so a server dribbling a byte every few seconds never trips it and
      `iter_bytes()` runs forever. The per-company budget cannot help either, because it
      is only checked between requests and the worker is stuck inside one.
    """
    deadline = time.monotonic() + settings.detect_timeout_seconds

    with client.stream(
        "GET", url, timeout=settings.detect_timeout_seconds
    ) as response:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= settings.detect_max_bytes or time.monotonic() >= deadline:
                break
        body = b"".join(chunks)

    # Rebuild a plain Response so callers keep `.text`, `.url` and `.status_code`.
    #
    # `iter_bytes()` yields bytes that are already decompressed, so the transfer headers
    # must not be carried over: leaving `Content-Encoding: gzip` in place makes the new
    # Response try to gunzip plain HTML, `.text` comes back as mojibake, and every ATS
    # fingerprint silently stops matching on exactly the sites that compress — which is
    # nearly all of them. `Content-Length` is dropped for the same reason: the body has
    # been truncated and the original length is now a lie.
    headers = httpx.Headers(
        {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length"}
        }
    )
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=body,
        request=response.request,
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
    for match in WORKDAY_PATTERN.finditer(text):
        tenant, host, site = match.group(1), match.group(2), match.group(3)
        # Not SLUG_BLOCKLIST: that list filters boilerplate out of single-slug boards,
        # where `boards.greenhouse.io/careers` names nothing. A Workday tenant host is
        # already specific, and "Careers" and "Jobs" are among the commonest real site
        # names - Broadridge's board is `broadridge.wd5.myworkdayjobs.com/Careers`, and
        # every tenant named that way was invisible to detection.
        if site.lower() not in WORKDAY_NON_SITES:
            return "workday", f"{tenant}:{host}:{site}"

    # Oracle, like Workday, is addressed by more than one part: the host *and* the site
    # number. The generic pattern below captures only the pod, which no request can use.
    from jobfinder.sources.oracle_recruiting import slug_from_url

    oracle = slug_from_url(text)
    if oracle:
        return "oracle_recruiting", oracle

    # Oleeo and CandidateManager are also two-part addresses. Both host the boards of
    # Irish public bodies and retailers, linked from careers pages that carry no other
    # fingerprint, so without these the companies read as having nothing to crawl.
    from jobfinder.sources import candidatemanager, oleeo

    for adapter, module in (("oleeo", oleeo), ("candidatemanager", candidatemanager)):
        found = module.slug_from_url(text)
        if found:
            return adapter, found

    for adapter, pattern in ATS_PATTERNS:
        for found in pattern.finditer(text):
            slug = found.group(1)
            lowered = slug.lower()
            if lowered in SLUG_BLOCKLIST:
                continue
            # `{slug}.personio.de` also matches Personio's own site in a "powered by"
            # footer, which would register the vendor instead of the customer.
            if adapter in HOST_BLOCKLIST and lowered in HOST_BLOCKLIST:
                continue
            if adapter == "eightfold":
                # `hp-sandbox.eightfold.ai` is linked from hp.com; the live site is `hp`.
                slug = TEST_TENANT_SUFFIX.sub("", slug)
            return adapter, slug
    return None


def careers_links(html: str, base_url: str) -> list[str]:
    """Careers-page URLs linked from a page, most promising first.

    Ranked so that a link whose href says `careers` outranks one where only the anchor
    text hints at it — the href is the more reliable signal, and following the best two
    or three is what keeps the sweep affordable.
    """
    base_host = urlsplit(base_url).netloc.lower()
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for href, inner in ANCHOR.findall(html):
        href = href.strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue

        text = TAGS.sub(" ", inner)
        href_hit = bool(CAREERS_WORDS.search(href))
        text_hit = bool(CAREERS_WORDS.search(text))
        if not (href_hit or text_hit):
            continue

        host = urlsplit(absolute).netloc.lower()
        if _is_non_careers_host(host):
            continue
        # An off-site careers link usually *is* the ATS, which is the best case there is.
        offsite = host != base_host and base_host not in host and host not in base_host

        score = (2 if href_hit else 0) + (1 if text_hit else 0) + (3 if offsite else 0)
        seen.add(absolute)
        scored.append((score, absolute))

    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    return [url for _, url in scored]


def detect_from_url(url: str, client: httpx.Client | None = None) -> Detection:
    """Fetch one URL and inspect it (and its redirect chain) for an ATS."""
    owns = client is None
    client = client or build_client()
    try:
        response = fetch_capped(client, url)
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


def normalize_website(website: str) -> str:
    website = website.strip().rstrip("/")
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def detect_for_website(
    website: str, client: httpx.Client | None = None, name: str | None = None
) -> Detection:
    """Find a company's ATS, following its own careers link rather than guessing.

    Returns the best result found. When no ATS is identified but a careers page was,
    the careers URL is still reported: that is what the portal links to for companies
    it cannot crawl, so they stay visible instead of silently vanishing.
    """
    website = normalize_website(website)
    budget = Budget()

    owns = client is None
    client = client or build_client()
    try:
        # 1. The homepage. Often carries the ATS fingerprint directly in a footer or a
        #    "we're hiring" widget, in which case one request is the whole job. Its body
        #    is kept rather than re-fetched, because step 2 needs the same markup.
        home, html = _detect_and_body(website, client)
        if home.found:
            return home

        best_careers_url: str | None = None

        # 2. Careers links the site itself advertises.
        #
        #    The board is often one hop *past* the careers page rather than on it:
        #    stripe.com/careers is a recruiting brochure whose "see open roles" button
        #    leads to the page that embeds Greenhouse. Stopping at the careers page
        #    misses those entirely, so each careers page is re-scanned for links and the
        #    most promising of those is followed too.
        for url in careers_links(html, website)[:MAX_CAREERS_LINKS]:
            if budget.expired:
                break
            result, page = _detect_and_body(url, client)
            if result.found:
                return result
            if best_careers_url is None and _looks_like_careers(url):
                best_careers_url = result.careers_url or url

            for deeper in careers_links(page, url)[:MAX_SECOND_HOP_LINKS]:
                if deeper == url or budget.expired:
                    continue
                deep_result = detect_from_url(deeper, client=client)
                if deep_result.found:
                    return deep_result

        # 3. Guessed paths and careers subdomains, for sites whose navigation is
        #    rendered client-side and therefore invisible to link discovery. This is
        #    most of the large multinationals.
        for candidate in _guessed_candidates(website):
            if budget.expired:
                break
            result = detect_from_url(candidate, client=client)
            if result.found:
                return result
            if best_careers_url is None and result.note == "no ATS fingerprint found":
                best_careers_url = result.careers_url

        # 4. Ask the boards directly.
        #
        #    Fingerprinting can only find an ATS the page admits to. Stripe's board is
        #    Greenhouse and `boards-api.greenhouse.io/v1/boards/stripe` serves it, but
        #    stripe.com renders its listings client-side and never names Greenhouse in
        #    the markup — so no amount of page-reading will find it. Asking each
        #    platform whether it hosts a board under the company's own domain name
        #    settles that in one request per platform, and a populated board is proof
        #    rather than inference.
        # The slug probe is the last step and the cheapest per request, so it runs even
        # on a thin remaining budget rather than being skipped wholesale.
        probed = probe_platforms(
            slug_candidates(website, name),
            client=client,
            budget=budget,
            expected_name=name,
        )
        if probed.found:
            probed.careers_url = best_careers_url or home.careers_url
            return probed

        return Detection(
            careers_url=best_careers_url or home.careers_url,
            note="careers page found, no ATS fingerprint"
            if best_careers_url
            else "no ATS fingerprint found",
        )
    finally:
        if owns:
            client.close()


# How many slug guesses to try per platform. Each candidate costs one request per
# platform, so this is the multiplier on the most expensive step in detection.
MAX_SLUG_CANDIDATES = 2

NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")

# Words that are part of a company's legal or descriptive name but never part of its
# board slug.
NAME_NOISE = re.compile(
    r"\b(plc|ltd|limited|group|holdings|international|ireland|irish|the|inc|llc|"
    r"technologies|technology|services|solutions|company|co)\b",
    re.I,
)


def slug_candidates(website: str, name: str | None = None) -> list[str]:
    """Board slugs a company is most likely to use, best first.

    The domain name leads because it is right far more often than not — Stripe's board
    is `stripe`. The company name is the fallback for the cases where the two differ:
    Datadog's domain is `datadoghq.com` but its Greenhouse board is `datadog`.
    """
    candidates: list[str] = []

    host = urlsplit(website).netloc.lower().removeprefix("www.")
    label = host.split(".")[0]
    if label:
        candidates.append(label)

    if name:
        cleaned = NAME_NOISE.sub(" ", name.lower())
        collapsed = NON_SLUG_CHARS.sub("", cleaned)
        if collapsed and collapsed not in candidates:
            candidates.append(collapsed)

    return [c for c in candidates if c and c not in SLUG_BLOCKLIST][:MAX_SLUG_CANDIDATES]


# Each platform's cheapest "does this board exist and have postings?" probe, as
# (url template, predicate on the parsed JSON). A board that exists but is empty is
# rejected: an empty board is indistinguishable from a wrong guess, and registering a
# wrong guess is far worse than missing a company that currently has no vacancies.
PLATFORM_PROBES: list[tuple[str, str]] = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
    ("lever", "https://api.lever.co/v0/postings/{slug}?mode=json"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}"),
    ("recruitee", "https://{slug}.recruitee.com/api/offers/"),
    ("personio", "https://{slug}.jobs.personio.de/xml"),
    ("hirehive", "https://{slug}.hirehive.com/api/v2/jobs"),
    ("breezy", "https://{slug}.breezy.hr/json"),
    ("pinpoint", "https://{slug}.pinpointhq.com/postings.json"),
]


def _probe_count(adapter: str, payload) -> int:
    if adapter in ("lever", "breezy"):
        return len(payload) if isinstance(payload, list) else 0
    if not isinstance(payload, dict):
        return 0
    if adapter == "recruitee":
        return len(payload.get("offers") or [])
    if adapter == "hirehive":
        return len(payload.get("items") or [])
    if adapter == "pinpoint":
        return len(payload.get("data") or [])
    return len(payload.get("jobs") or [])


# The shortest slug a probe may claim without the board naming its own owner. Short
# slugs are common English words that many unrelated companies share — `meta.recruitee.com`
# exists and is not Meta — so an unverifiable short match is far more likely to be a
# collision than a discovery.
MIN_UNVERIFIED_SLUG_LENGTH = 6


def _declared_owner(adapter: str, payload) -> str | None:
    """The company name the board itself claims, where the platform publishes one."""
    if adapter == "recruitee" and isinstance(payload, dict):
        offers = payload.get("offers") or []
        if offers and isinstance(offers[0], dict):
            return offers[0].get("company_name")
    if adapter == "breezy":
        from jobfinder.sources.breezy import board_owner

        return board_owner(payload)
    return None


def _personio_owner(xml: str) -> str | None:
    """Personio feeds name the hiring entity in `<subcompany>`."""
    match = re.search(r"<subcompany>\s*([^<]+?)\s*</subcompany>", xml, re.I)
    return match.group(1) if match else None


def _same_company(declared: str, expected: str) -> bool:
    """Do two company names refer to the same employer?

    Equality after the registry's own normalization, which already strips the suffixes
    and country words that differ between a legal name and a careers-page name — so
    "EY" matches "EY Ireland" and "Acme" matches "Acme Group Ireland Ltd" without any
    fuzzy matching being needed.

    Looser rules were tried and both let real errors through. Substring matching pairs
    "Meta" with "Metafoor". Subset-of-words matching pairs "ICON plc" with "Icon
    Talent" — a different company that really does run a Recruitee board — because
    {icon} is a subset of {icon, talent}. An extra word in the board owner's name is
    evidence it is a *different* employer, not the same one described more fully, and
    normalization has already removed the words that would legitimately differ.
    """
    from jobfinder.normalize.dedup import normalize_company_name

    left = normalize_company_name(declared)
    right = normalize_company_name(expected)
    return bool(left) and left == right


def _board_matches(
    adapter: str, url: str, client: httpx.Client, slug: str, expected_name: str | None
) -> bool:
    """Does this platform serve a populated board that belongs to *this* company?

    Two separate questions, and both have to be yes.

    **Is it populated?** Emptiness is treated as absence, because an empty board and a
    wrong guess are the same response.

    **Is it theirs?** This is the one a slug probe can get badly wrong. Probing is
    inference from a name collision: `meta.recruitee.com` is a real, populated Recruitee
    board belonging to an unrelated company, and an earlier version of this registered it
    as Meta's — filing a stranger's vacancies under a household name. So where the
    platform states who owns the board, that statement must agree with the company being
    probed; where it does not, only a slug distinctive enough to be unlikely to collide
    is accepted. Missing an employer costs a sweep; misattributing one corrupts the data
    and the user cannot tell.
    """
    try:
        response = fetch_capped(client, url)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False

    if adapter == "personio":
        # XML rather than JSON; a real feed names its positions.
        if "<position>" not in response.text:
            return False
        declared = _personio_owner(response.text)
        if declared and expected_name:
            return _same_company(declared, expected_name)
        return len(slug) >= MIN_UNVERIFIED_SLUG_LENGTH

    try:
        payload = response.json()
    except ValueError:
        return False
    if _probe_count(adapter, payload) <= 0:
        return False

    declared = _declared_owner(adapter, payload)
    if declared and expected_name:
        return _same_company(declared, expected_name)

    return len(slug) >= MIN_UNVERIFIED_SLUG_LENGTH


def probe_platforms(
    slugs: str | list[str],
    client: httpx.Client | None = None,
    budget: Budget | None = None,
    expected_name: str | None = None,
) -> Detection:
    """Ask each ATS whether it hosts this company's board under any candidate slug."""
    if isinstance(slugs, str):
        slugs = [slugs]
    slugs = [s for s in slugs if s and s not in SLUG_BLOCKLIST]
    if not slugs:
        return Detection(note="no usable slug candidate")

    owns = client is None
    client = client or build_client()
    try:
        # Candidate-major: the domain-derived slug is right most of the time, so every
        # platform is tried on it before the second candidate costs anything.
        for slug in slugs:
            for adapter, template in PLATFORM_PROBES:
                if budget is not None and budget.expired:
                    return Detection(note="detection budget exhausted")
                if _board_matches(
                    adapter,
                    template.format(slug=slug),
                    client,
                    slug,
                    expected_name,
                ):
                    return Detection(
                        adapter=adapter,
                        slug=slug,
                        confidence="medium",
                        note="board found by slug probe",
                    )

        return Detection(note="no board found by slug probe")
    finally:
        if owns:
            client.close()


def _looks_like_careers(url: str) -> bool:
    return bool(CAREERS_WORDS.search(url))


def _guessed_candidates(website: str) -> list[str]:
    """Careers URLs to try when the site's own links revealed nothing.

    Subdomains come first: `careers.example.com` existing at all is strong evidence,
    whereas `example.com/careers` is a guess that frequently 404s.
    """
    host = urlsplit(website).netloc
    candidates = [f"https://{sub}.{host}" for sub in CAREERS_SUBDOMAINS]
    candidates += [f"{website}{path}" for path in CAREERS_PATHS[:MAX_GUESSED_PATHS]]
    return candidates


def _detect_and_body(url: str, client: httpx.Client) -> tuple[Detection, str]:
    """`detect_from_url`, but also returning the markup so it need not be fetched twice.

    Across a registry of thousands, one saved request per company is thousands of
    requests — the same reasoning that replaced guessed paths with link discovery.
    """
    try:
        response = fetch_capped(client, url)
    except httpx.HTTPError as exc:
        return Detection(careers_url=url, note=f"fetch failed: {exc!r}"), ""

    final_url = str(response.url)

    hit = detect_in_text(final_url)
    if hit:
        return (
            Detection(
                adapter=hit[0], slug=hit[1], careers_url=final_url,
                confidence="high", note="redirect target",
            ),
            response.text,
        )

    if response.status_code >= 400:
        return Detection(careers_url=url, note=f"HTTP {response.status_code}"), ""

    hit = detect_in_text(response.text)
    if hit:
        return (
            Detection(
                adapter=hit[0], slug=hit[1], careers_url=final_url,
                confidence="high", note="embedded in page",
            ),
            response.text,
        )

    return (
        Detection(careers_url=final_url, note="no ATS fingerprint found"),
        response.text,
    )
