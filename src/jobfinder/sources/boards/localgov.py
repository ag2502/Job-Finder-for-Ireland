"""localgovernmentjobs.ie, the Local Government Management Agency's shared board.

    https://www.localgovernmentjobs.ie/Search/SearchResultsWithFilter?...&showGroup=200

Most of Ireland's 31 local authorities advertise here: county and city councils, the
regional assemblies and the LGMA itself. That is where general operatives, library
assistants, lifeguards, school wardens and retained firefighters are recruited, much of
it part-time and nearly none of it on an ATS, so the councils were otherwise almost
entirely uncrawled.

The search page's own script asks one endpoint for a page of result cards, and a large
`showGroup` returns every card at once (47 when this was written). Each card names the
role, the council, the closing date, grade and salary; the advert's own page adds the
county, the contract type ("Permanent & Fixed Term", "Part-time") and the full text, so
each one is read too. The site has no robots.txt and no terms restricting reuse.
"""

from __future__ import annotations

import re

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

ORIGIN = "https://www.localgovernmentjobs.ie"
SEARCH = (
    ORIGIN + "/Search/SearchResultsWithFilter?searchText=&categoryFilter=&employerFilter="
    "&locationFilter=&salaryFilter=&pageNumber=1&showGroup={size}"
)
# Far above the board's size; a full board at this ceiling is read as a sample.
PAGE_SIZE = 400

ADVERTISED = re.compile(r"Advertised Date:\s*(.+)", re.I)
# "Dun Laoghaire Rathdown County Council" -> where it is, for adverts that leave the
# location blank. The Dublin authorities are not named after the county.
_DUBLIN_AUTHORITIES = re.compile(
    r"dublin|fingal|d[uú]n laoghaire|local government management|eastern and midland", re.I
)
_AUTHORITY_SUFFIX = re.compile(
    r"\s*(?:city and county|city & county|county|city)?\s*council\s*$", re.I
)


_PATH_FOLDER = re.compile(r"^/jobs/(?:Archived-)?(.+?)(?:-Jobs?)?/", re.I)


def council_from_path(path: str) -> str:
    """The council a card's folder names, for cards that leave it blank.

    Some still-open adverts sit under an "Archived-Monaghan-County-Council-Jobs" folder
    and print no council at all.
    """
    match = _PATH_FOLDER.match(path)
    return match.group(1).replace("-", " ") if match else ""


def authority_place(council: str) -> str:
    """Where a council is, as a place the location parser reads."""
    if _DUBLIN_AUTHORITIES.search(council):
        return "Dublin, Ireland"
    place = _AUTHORITY_SUFFIX.sub("", council).strip()
    return f"{place}, Ireland" if place else "Ireland"


def parse_cards(html: str) -> list[RawJob]:
    """The postings on the results page, before their adverts are read."""
    jobs: list[RawJob] = []
    for card in HTMLParser(html).css("div.job-cards-item"):
        link = card.css_first('a[href^="/jobs/"]')
        title_node = card.css_first(".job-cards-council h2")
        council_node = card.css_first(".job-cards-council p")
        if link is None or title_node is None:
            continue
        path = link.attributes.get("href") or ""
        council = (council_node.text(strip=True) if council_node else "") or council_from_path(path)
        facts = [p.text(separator=" ", strip=True) for p in card.css(".job-cards-date p")]
        posted = next((m.group(1) for m in map(ADVERTISED.search, facts) if m), None)
        location = next(
            (f.split(":", 1)[1].strip() for f in facts if f.lower().startswith("location:")), ""
        )
        jobs.append(
            RawJob(
                source_job_id=path.removeprefix("/jobs/"),
                title=title_node.text(strip=True),
                url=ORIGIN + path,
                location_raw=f"{location}, Ireland" if location else authority_place(council),
                description="\n".join(f for f in facts if ":" in f) or None,
                posted_at=_date(posted),
                company_name=council or None,
            )
        )
    return jobs


def read_advert(job: RawJob, html: str) -> None:
    """Fill in the county, contract type and full text from the advert's own page."""
    tree = HTMLParser(html)
    info = [
        node.text(separator=" ", strip=True)
        for node in tree.css(".job-info-content .col-2 div")
    ]
    # Weekly Rate | Meath | Permanent & Fixed Term | Closing Date: ...
    county = info[1] if len(info) > 1 and ":" not in info[1] else ""
    if county and county.lower() not in ("ireland", "various", "nationwide"):
        job.location_raw = f"{county}, Ireland"
    position = tree.css_first(".job-info-position")
    if position is not None:
        job.employment_type = position.text(strip=True) or None
    body = tree.css_first(".job-description-copy")
    if body is not None:
        job.description = "\n".join(filter(None, [job.description, body.html]))


def _date(value: str | None):
    if not value:
        return None
    try:
        return date_parser.parse(value, dayfirst=True)
    except (ValueError, OverflowError):
        return None


class LocalGovernmentAdapter(BaseAdapter):
    """Every vacancy on localgovernmentjobs.ie, filed under the council that posted it.

    The slug is unused beyond naming the source; there is one board.
    """

    name = "localgov"
    tier = 4

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(SEARCH.format(size=PAGE_SIZE))
        response.raise_for_status()
        jobs = parse_cards(response.text)
        if not jobs and "job-cards" not in response.text:
            raise ValueError("localgovernmentjobs.ie returned no result list")

        for job in jobs:
            # An advert that fails to load keeps its card; the card alone is a posting.
            try:
                advert = client.get(job.url)
                advert.raise_for_status()
                read_advert(job, advert.text)
            except httpx.HTTPError:
                pass
            self.polite_pause()

        if len(jobs) >= PAGE_SIZE:
            return PartialJobs(jobs)
        return jobs


register(LocalGovernmentAdapter())
