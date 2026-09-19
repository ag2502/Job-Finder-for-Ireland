"""Browser-assisted detection for careers pages built in JavaScript.

Some careers pages hold nothing until a script runs: the vacancy list is fetched from a
job board's API after the page loads, so the markup detection reads never names the
board. Adyen's careers site is empty HTML that then calls
`boards-api.greenhouse.io/v1/boards/adyen`; Mason Hayes & Curran's calls HireHive's API.

This step renders each still-blocked careers page once in a headless browser, records
every URL the page requests, and runs the ordinary fingerprints over them — and over the
rendered page if the requests named nothing. A board found this way is registered
through the same checks as any other (a populated board, and the ownership rule), and
from then on it is crawled over its public API by plain HTTP.

## Why the browser never enters the crawl

A browser is slow, heavy and fragile, and the six-hourly crawl is none of those. So the
browser is used only to *discover* where a site's jobs come from, weekly, in the registry
job; what it discovers is always a board the crawler can read without one. Reading job
lists straight out of arbitrary JSON responses was tried against the blocked queue and
rejected: nearly everything a careers page fetches is a menu, a form or a feature flag,
and telling those from a vacancy list without a schema is guesswork.

Playwright is an optional dependency (`pip install -e ".[render]"` and
`playwright install chromium`); without it this step reports that and does nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from jobfinder.core.models import Company
from jobfinder.registry.detect import Detection, detect_in_text

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 25_000
SETTLE_MS = 1_500
# A hard ceiling per page, past navigation's own timeout: a page can hang in `content()`
# or `close()` as well, and one stuck tab must not hold the weekly job.
PAGE_DEADLINE_SECONDS = 60
CONCURRENCY = 6
COMMIT_EVERY = 25

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 JobFinderBot/0.1"
)


@dataclass
class Evidence:
    requested: list[str] = field(default_factory=list)
    rendered: str = ""
    error: str | None = None


@dataclass
class RenderStats:
    rendered: int = 0
    found: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"rendered {self.rendered}: {self.found} named a job board, "
            f"{self.failed} could not be rendered"
        )


def detection_from(evidence: Evidence, careers_url: str) -> Detection:
    """What the rendered page reveals, requests before markup.

    A request the page actually made is the stronger evidence: the page's own code chose
    to load that board. A link in the rendered markup may be anything — a partner, a news
    story — which is how a hop from Zoom's careers page once reached a news site's board.
    """
    for source, note in (
        ("\n".join(evidence.requested), "requested while rendering"),
        (evidence.rendered, "in rendered page"),
    ):
        hit = detect_in_text(source) if source else None
        if hit:
            return Detection(
                adapter=hit[0], slug=hit[1], careers_url=careers_url,
                confidence="high", note=note,
            )
    return Detection(careers_url=careers_url, note="careers page found, no ATS fingerprint")


async def _render(browser, url: str) -> Evidence:
    evidence = Evidence()
    context = await browser.new_context(user_agent=BROWSER_UA)
    try:
        page = await context.new_page()
        page.on("request", lambda request: evidence.requested.append(request.url))
        try:
            await page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - a slow page still yields its requests
            evidence.error = repr(exc)
        await page.wait_for_timeout(SETTLE_MS)
        try:
            evidence.rendered = await page.content()
        except Exception:  # noqa: BLE001 - page crashed or navigated away
            pass
    finally:
        await context.close()
    return evidence


async def _render_all(targets: list[tuple[int, str]]) -> list[tuple[int, Evidence]]:
    from playwright.async_api import async_playwright

    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            async def one(company_id: int, url: str):
                async with semaphore:
                    try:
                        evidence = await asyncio.wait_for(
                            _render(browser, url), timeout=PAGE_DEADLINE_SECONDS
                        )
                    except Exception as exc:  # noqa: BLE001 - includes the deadline
                        evidence = Evidence(error=repr(exc))
                    return company_id, evidence

            return await asyncio.gather(*(one(cid, url) for cid, url in targets))
        finally:
            await browser.close()


def playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return False
    return True


def render_blocked(
    session: Session, *, limit: int | None = None, dry_run: bool = False
) -> RenderStats:
    """Render each blocked company's careers page and register any board it reveals."""
    from jobfinder.registry.bulk_detect import SweepStats, _apply
    from jobfinder.registry.extraction import blocked_candidates
    from jobfinder.sources import load_adapters
    from jobfinder.sources.base import all_adapters

    stats = RenderStats()
    if not playwright_available():
        logger.warning("playwright is not installed; skipping browser-assisted detection")
        return stats

    companies: list[Company] = blocked_candidates(session, limit=limit)
    if not companies:
        return stats
    logger.info("rendering %d blocked careers pages", len(companies))

    by_id = {c.id: c for c in companies}
    results = asyncio.run(_render_all([(c.id, c.careers_url or "") for c in companies]))

    sweep = SweepStats()
    load_adapters()
    known = set(all_adapters())
    for count, (company_id, evidence) in enumerate(results, start=1):
        company = by_id[company_id]
        stats.rendered += 1
        if evidence.error and not evidence.requested:
            stats.failed += 1
            continue
        detection = detection_from(evidence, company.careers_url or "")
        if not detection.found:
            continue
        stats.found += 1
        logger.info("%s: %s:%s (%s)", company.name, detection.adapter, detection.slug, detection.note)
        _apply(
            session, company=company, detection=detection, stats=sweep,
            known_adapters=known, client=None, dry_run=dry_run,
        )
        if count % COMMIT_EVERY == 0:
            session.commit()

    session.flush()
    logger.info("browser-assisted detection: %s", sweep)
    return stats
