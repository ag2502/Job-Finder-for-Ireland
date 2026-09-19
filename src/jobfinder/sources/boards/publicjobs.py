"""publicjobs.ie — the Public Appointments Service's recruitment board.

    https://publicjobs.tal.net/vx/.../candidate/jobboard/vacancy/{board}/adv/?start={n}

Every Irish civil and public service competition is advertised here: government
departments, An Garda Síochána, local authorities, state boards and HSE consultants. The
board runs on Oleeo, but its Atom feed is switched off, so the adapter reads the search
results page instead. That page is enough on its own: each result names the role, the
recruiting organisation, the location and the advertising date, so no per-job request
is needed and a full read of every board is about eight pages.

The slug is the Oleeo board number: 3 is the main board, 6 medical consultants, 7 state
boards. `robots.txt` asks for ten seconds between requests, which is honoured here in
place of the usual per-host delay.
"""

from __future__ import annotations

import re
import time

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy

ORIGIN = "https://publicjobs.tal.net"
BOARD = ORIGIN + "/vx/lang-en-GB/mobile-0/appcentre-ext/brand-4/candidate/jobboard/vacancy/{board}/adv/"
PAGE_SIZE = 50
MAX_PAGES = 12
CRAWL_DELAY_SECONDS = 10

OPP_ID = re.compile(r"/opp/(\d+)-")
TOTAL = re.compile(r"([\d,]+)\s+results?\s+match", re.I)
LABELS = ("Vacancy type", "Department/Organisation", "Location", "Advertising Date", "Closing Date")


def parse_results(html: str) -> tuple[list[RawJob], int | None]:
    """The postings on one results page, and the board's total result count."""
    tree = HTMLParser(html)
    jobs: list[RawJob] = []
    for row in tree.css("div.details_row"):
        link = row.css_first('a[href*="/opp/"]')
        if link is None:
            continue
        url = link.attributes.get("href") or ""
        match = OPP_ID.search(url)
        if not match:
            continue
        fields = _fields(row.text(separator="\n", strip=True))
        organisation = fields.get("Department/Organisation")
        posted = fields.get("Advertising Date")
        summary = [
            f"{label}: {fields[label]}" for label in LABELS if fields.get(label)
        ]
        jobs.append(
            RawJob(
                source_job_id=match.group(1),
                title=link.text(strip=True),
                url=url,
                location_raw=_irish(fields.get("Location")),
                description="\n".join(summary) or None,
                posted_at=_date(posted),
                company_name=organisation,
            )
        )
    total = TOTAL.search(tree.body.text() if tree.body else "")
    return jobs, int(total.group(1).replace(",", "")) if total else None


def _fields(text: str) -> dict[str, str]:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[:-1]):
        label = line.rstrip(":").strip()
        if label in LABELS and line.endswith(":"):
            fields[label] = lines[index + 1]
    return fields


def _irish(location: str | None) -> str | None:
    """Every role here is in Ireland; saying so lets "Dublin" be read without doubt."""
    if not location:
        return None
    return location if "ireland" in location.lower() else f"{location}, Ireland"


def _date(value: str | None):
    try:
        return date_parser.parse(value, dayfirst=True) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


class PublicJobsAdapter(BaseAdapter):
    name = "publicjobs"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        url = BOARD.format(board=slug)
        if not RobotsPolicy(ORIGIN, client).allows(url):
            raise PermissionError(f"robots.txt disallows {url}")

        jobs: dict[str, RawJob] = {}
        total: int | None = None
        for page in range(MAX_PAGES):
            if page:
                time.sleep(CRAWL_DELAY_SECONDS)
            response = client.get(url, params={"start": page * PAGE_SIZE} if page else None)
            response.raise_for_status()
            found, total = parse_results(response.text)
            for job in found:
                jobs.setdefault(job.source_job_id, job)
            if not found or (total is not None and (page + 1) * PAGE_SIZE >= total):
                break
        else:
            return PartialJobs(jobs.values())

        if total is not None and len(jobs) < total:
            # A result that vanished between pages is possible; one never reached is not.
            if len(jobs) < total * 0.9:
                return PartialJobs(jobs.values())
        return list(jobs.values())


register(PublicJobsAdapter())
