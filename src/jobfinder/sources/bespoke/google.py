"""Google careers adapter.

Google has no public jobs API — the old Jobs API was discontinued and
`careers.google.com/api/v3/search` returns "Not Found". But the careers results page is
**server-rendered**, so the listings are present in the raw HTML and no headless browser
is required:

    https://www.google.com/about/careers/applications/jobs/results/?location=Dublin,%20Ireland&page=N

Roles appear as links of the form ``jobs/results/{id}-{slug}``, 20 per page, with
`page` paginating cleanly. The human-readable title is recovered from the URL slug,
which is lossy — hyphens replace punctuation — so a detail fetch fills in the real title
and description.

Because this parses markup rather than a contract, it is the adapter most likely to
break when Google restyles the page. It fails loudly: if the ID pattern stops matching,
the adapter raises rather than reporting an empty board, so reconciliation records
FAILED and no Google jobs are closed on the strength of a layout change.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results/"
JOB_LINK = re.compile(r"jobs/results/(\d+)-([a-z0-9\-]+)")
MAX_PAGES = 15

# Boilerplate headings that are not a role title.
_JUNK_TITLES = {"job details", "job", "jobs", "google careers", ""}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


def title_from_slug(slug: str) -> str:
    """Best-effort title recovery from a URL slug."""
    return " ".join(word.capitalize() for word in slug.split("-") if word)


class GoogleAdapter(BaseAdapter):
    name = "google"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        location = slug or "Dublin, Ireland"
        seen: dict[str, str] = {}

        for page in range(1, MAX_PAGES + 1):
            response = client.get(
                RESULTS_URL,
                params={"location": location, "page": page},
                headers=BROWSER_HEADERS,
            )
            response.raise_for_status()
            html = response.text

            matches = JOB_LINK.findall(html)
            if page == 1 and not matches:
                # Fail loudly rather than silently reporting an empty board: an empty
                # OK result would begin closing every Google job.
                raise ValueError(
                    "google careers page returned no job links - markup likely changed"
                )
            if not matches:
                break

            new = {job_id: job_slug for job_id, job_slug in matches if job_id not in seen}
            if not new:
                break  # pagination exhausted; same page repeating
            seen.update(new)
            self.polite_pause()

        jobs: list[RawJob] = []
        for job_id, job_slug in seen.items():
            url = f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{job_slug}"
            title, description, detail_location = self._detail(url, client)
            jobs.append(
                RawJob(
                    source_job_id=job_id,
                    title=title or title_from_slug(job_slug),
                    url=url,
                    location_raw=detail_location or location,
                    description=description,
                )
            )
        return jobs

    def _detail(
        self, url: str, client: httpx.Client
    ) -> tuple[str | None, str | None, str | None]:
        """Fetch the real title, description and location. Best effort."""
        try:
            response = client.get(url, headers=BROWSER_HEADERS)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("google detail fetch failed for %s: %s", url, exc)
            return None, None, None

        tree = HTMLParser(response.text)

        # The <h1> on these pages is the boilerplate "job details", not the role, so
        # reading it made every Google posting share one meaningless title. The real
        # title lives in og:title, with <title> (minus its site suffix) as a backstop.
        title = None
        og = tree.css_first('meta[property="og:title"]')
        if og:
            title = (og.attributes.get("content") or "").strip()

        if not title:
            node = tree.css_first("title")
            if node:
                title = re.split(r"\s+[—|–-]\s+Google", node.text(strip=True))[0].strip()

        if title and title.lower() in _JUNK_TITLES:
            title = None

        description = None
        body = tree.css_first("div.KwJkGe") or tree.css_first("main")
        if body:
            description = body.text(separator="\n", strip=True)[:20000]

        location = None
        for node in tree.css("span, div"):
            text = node.text(strip=True)
            if text and "Dublin" in text and len(text) < 80:
                location = text
                break

        self.polite_pause()
        return title, description, location


register(GoogleAdapter())
