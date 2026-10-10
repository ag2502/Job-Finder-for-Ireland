"""Reading careers events from the sites that list them.

Four readers, each turning one site's listing into `RawEvent`s:

* **Eventbrite**: its Ireland search pages carry every result as JSON in the page
  (`window.__SERVER_DATA__`), with the start time, venue and Eventbrite's own category
  tags. The "Career" subcategory is the strongest signal any source gives. Intreo's
  Work and Skills fairs are published here, and so are most college careers fairs.
  Eventbrite closed its search API in 2020; the search pages are allowed by its
  robots.txt.
* **Meetup**: the Tech and Career & Business categories for each city, read from the
  schema.org Event data in the page. Its robots.txt disallows `source=` and `keywords=`
  in a find URL, so neither is used.
* **Luma**: the city page, schema.org Event data again.
* **gradireland**: the events hub's page data (a Gatsby site, so the page itself is an
  empty shell and the JSON beside it is the real content).

A reader never raises: a page that fails is logged and skipped, and the others carry on.
Nothing here writes; `events.crawl` decides what to keep.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from jobfinder.normalize.location import IRISH_COUNTIES, irish_region

logger = logging.getLogger(__name__)

DUBLIN = ZoneInfo("Europe/Dublin")
# Between two requests to the same site.
COURTESY_SECONDS = 1.0

# Eventbrite searches, all across Ireland. Its search is loose ("jobs fair" also finds
# networking evenings), which `relevance` deals with; between them these cover the job
# fairs, Intreo's events, college careers fairs and recruitment days.
EVENTBRITE_QUERIES = (
    "jobs-fair", "job-fair", "careers-fair", "career-fair", "recruitment",
    "recruitment-fair", "hiring", "intreo", "work-and-skills", "graduate", "employment",
    "careers", "cv-workshop", "internship", "apprenticeship", "tech-careers",
)
EVENTBRITE_PAGES = 3

# Meetup: Technology (546) and Career & Business (405), in the cities with groups.
MEETUP_CITIES = ("Dublin", "Cork", "Galway", "Limerick")
MEETUP_CATEGORIES = {546: True, 405: False}   # category id: is it a tech listing

LUMA_CITIES = ("dublin",)


@dataclass
class RawEvent:
    source: str
    source_id: str
    url: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    has_time: bool = True
    summary: str = ""
    organizer: str | None = None
    image_url: str | None = None
    venue: str | None = None
    town: str | None = None
    region: str | None = None
    is_online: bool = False
    is_free: bool | None = None
    cancelled: bool = False
    # Eventbrite's own category names ("Career", "Business & Professional").
    tags: tuple[str, ...] = field(default_factory=tuple)
    # From a listing that is about technology, so a tech word is enough to keep it.
    tech_hint: bool = False


def _clean(text: str | None, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(text.split())[:limit]


def _local(day: str, clock: str | None, zone: str | None = None) -> tuple[datetime, bool]:
    """A listing's date and time of day, in Irish time unless it says otherwise, as UTC."""
    tz = ZoneInfo(zone) if zone else DUBLIN
    parsed = date.fromisoformat(day[:10])
    stamp = _clock(clock)
    if stamp is None:
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=tz).astimezone(timezone.utc), False
    hour, minute = stamp
    return datetime(parsed.year, parsed.month, parsed.day, hour, minute, tzinfo=tz).astimezone(timezone.utc), True


def _clock(value: str | None) -> tuple[int, int] | None:
    """"18:30", "6:30 PM", "10am" as (hour, minute); None for anything else."""
    match = re.match(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?", value or "", re.IGNORECASE)
    if not match or not (match.group(2) or match.group(3)):
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    half = (match.group(3) or "").lower().replace(".", "")
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 and minute < 60 else None


def _iso(value: str | None) -> tuple[datetime | None, bool]:
    """A schema.org date or date-time, as UTC, and whether it carried a time of day."""
    if not value:
        return None, False
    if len(value) <= 10:
        return _local(value, None)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=DUBLIN)
    return moment.astimezone(timezone.utc), True


def _region(*parts: str | None) -> str | None:
    text = ", ".join(p for p in parts if p)
    return irish_region(text, names_ireland=True) if text else None


def _only_county(text: str) -> str | None:
    named = {c for c in IRISH_COUNTIES if re.search(rf"\b{re.escape(c)}\b", text, re.IGNORECASE)}
    return named.pop() if len(named) == 1 else None


def _get(client: httpx.Client, url: str) -> str | None:
    try:
        response = client.get(url, headers={"Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    except httpx.HTTPError as exc:
        logger.warning("events: %s failed: %s", url, exc)
        return None
    finally:
        time.sleep(COURTESY_SECONDS)
    if response.status_code != 200:
        logger.warning("events: %s returned %s", url, response.status_code)
        return None
    return response.text


# ---------------------------------------------------------------- Eventbrite

def _server_data(page: str) -> dict | None:
    marker = page.find("__SERVER_DATA__")
    if marker < 0:
        return None
    start = page.find("{", marker)
    try:
        data, _ = json.JSONDecoder().raw_decode(page[start:])
    except ValueError:
        return None
    return data


def parse_eventbrite(page: str) -> tuple[list[RawEvent], int]:
    """One Eventbrite search page: its events and how many pages the search has."""
    data = _server_data(page)
    found = ((data or {}).get("search_data") or {}).get("events") or {}
    out: list[RawEvent] = []
    # Promoted results are adverts placed above the search, not results of it.
    for item in found.get("results") or []:
        venue = item.get("primary_venue") or {}
        address = venue.get("address") or {}
        online = bool(item.get("is_online_event"))
        if not online and (address.get("country") or "IE").upper() != "IE":
            continue
        if not item.get("start_date"):
            continue
        zone = item.get("timezone") or "Europe/Dublin"
        starts, has_time = _local(item["start_date"], item.get("start_time"), zone)
        ends = None
        if item.get("end_date"):
            ends, _ = _local(item["end_date"], item.get("end_time"), zone)
        image = item.get("image") or {}
        town = address.get("city")
        out.append(RawEvent(
            source="eventbrite",
            source_id=str(item.get("eid") or item.get("id")),
            url=(item.get("url") or "").split("?")[0],
            title=_clean(item.get("name"), 300),
            summary=_clean(item.get("summary")),
            starts_at=starts, ends_at=ends if ends and ends >= starts else None, has_time=has_time,
            image_url=(image.get("image_sizes") or {}).get("medium") or image.get("url"),
            venue=venue.get("name"),
            town=town,
            region=None if online else _region(town, address.get("region"),
                                                address.get("localized_address_display")),
            is_online=online,
            cancelled=bool(item.get("is_cancelled")),
            tags=tuple(t.get("display_name", "") for t in item.get("tags") or []
                       if t.get("prefix", "").startswith("Eventbrite")),
        ))
    pages = int((found.get("pagination") or {}).get("page_count") or 1)
    return out, pages


def eventbrite(client: httpx.Client) -> list[RawEvent]:
    out: list[RawEvent] = []
    for query in EVENTBRITE_QUERIES:
        page_no, pages = 1, 1
        while page_no <= min(pages, EVENTBRITE_PAGES):
            url = f"https://www.eventbrite.ie/d/ireland/{query}/"
            text = _get(client, url + (f"?page={page_no}" if page_no > 1 else ""))
            if text is None:
                break
            events, pages = parse_eventbrite(text)
            out.extend(events)
            # A loose search drifts off the subject a page or two in; once a page has
            # nothing tagged Career, the next will not either.
            if not any("Career" in e.tags for e in events):
                break
            page_no += 1
    return out


# -------------------------------------------------- schema.org (Meetup, Luma)

def _jsonld_events(page: str) -> list[dict]:
    """Every schema.org Event in a page's JSON-LD, however deeply it is nested."""
    found: list[dict] = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                kind = node.get("@type")
                if isinstance(kind, str) and kind.endswith("Event") and node.get("startDate"):
                    found.append(node)
                    continue
                stack.extend(v for v in node.values() if isinstance(v, (list, dict)))
    return found


def parse_jsonld(page: str, source: str, *, tech_hint: bool = False,
                 in_person_only: bool = False) -> list[RawEvent]:
    """Every Event in a page's schema.org data that takes place in Ireland.

    `in_person_only` drops online events, for a city page that also lists a group's
    talks streamed from anywhere (an OWASP chapter in Italy reached Dublin's Meetup).
    """
    out: list[RawEvent] = []
    for node in _jsonld_events(page):
        url = (node.get("url") or node.get("@id") or "").split("?")[0]
        starts, has_time = _iso(node.get("startDate"))
        if not url or starts is None:
            continue
        ends, _ = _iso(node.get("endDate"))
        location = node.get("location") or {}
        if isinstance(location, list):
            location = location[0] if location else {}
        online = (location.get("@type") == "VirtualLocation"
                  or "Online" in str(node.get("eventAttendanceMode") or ""))
        address = location.get("address") or {}
        if isinstance(address, str):
            address = {"streetAddress": address}
        country = str(address.get("addressCountry") or "IE")
        if online and in_person_only:
            continue
        if not online and country.upper() not in ("IE", "IRELAND"):
            continue
        organizer = node.get("organizer") or {}
        if isinstance(organizer, list):
            organizer = organizer[0] if organizer else {}
        image = node.get("image")
        if isinstance(image, list):
            image = image[-1] if image else None
        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = offers.get("price") if isinstance(offers, dict) else None
        try:
            free = None if price is None else float(price) == 0
        except (TypeError, ValueError):
            free = None
        town = address.get("addressLocality")
        out.append(RawEvent(
            source=source,
            source_id=urlsplit(url).path.strip("/") or url,
            url=url,
            title=_clean(node.get("name"), 300),
            summary=_clean(node.get("description")),
            starts_at=starts, ends_at=ends if ends and ends >= starts else None, has_time=has_time,
            organizer=organizer.get("name") if isinstance(organizer, dict) else None,
            image_url=image if isinstance(image, str) else None,
            venue=location.get("name") if not online else None,
            town=town,
            region=None if online else _region(town, address.get("addressRegion"),
                                                address.get("streetAddress")),
            is_online=online,
            is_free=free,
            cancelled="Cancelled" in str(node.get("eventStatus") or ""),
            tech_hint=tech_hint,
        ))
    return out


def meetup(client: httpx.Client) -> list[RawEvent]:
    out: list[RawEvent] = []
    for city in MEETUP_CITIES:
        for category, tech in MEETUP_CATEGORIES.items():
            text = _get(client, f"https://www.meetup.com/find/?location=ie--{city}&categoryId={category}")
            if text:
                out.extend(parse_jsonld(text, "meetup", tech_hint=tech, in_person_only=True))
    return out


def luma(client: httpx.Client) -> list[RawEvent]:
    out: list[RawEvent] = []
    for city in LUMA_CITIES:
        text = _get(client, f"https://luma.com/{city}")
        if text:
            out.extend(parse_jsonld(text, "luma", tech_hint=True, in_person_only=True))
    return out


# ---------------------------------------------------------------- gradireland

GRADIRELAND = "https://gradireland.com"


def parse_gradireland(text: str) -> list[RawEvent]:
    try:
        data = json.loads(text)["result"]["data"]
    except (ValueError, KeyError, TypeError):
        return []
    nodes = []
    for key in ("allEventSeries", "allEventChildren", "allOrphanEventChildren"):
        nodes.extend((data.get(key) or {}).get("nodes") or [])
    out: list[RawEvent] = []
    for node in nodes:
        start = node.get("field_event_start_date")
        alias = (node.get("path") or {}).get("alias")
        if not start or not alias:
            continue
        starts, has_time = _local(start, node.get("field_event_start_time_info"))
        ends = None
        if node.get("field_event_end_date"):
            ends, end_has_time = _local(node["field_event_end_date"], node.get("field_event_end_time_info"))
            if not end_has_time:
                ends += timedelta(days=1)   # to the end of the last day
        relations = node.get("relationships") or {}
        body = " ".join(
            (part.get("field_body") or {}).get("processed") or ""
            for part in relations.get("field_content") or [] if isinstance(part, dict)
        )
        thumb = ((((relations.get("field_thumbnail_image") or {}).get("localFile") or {})
                  .get("childImageSharp") or {}).get("gatsbyImageData") or {})
        src = ((thumb.get("images") or {}).get("fallback") or {}).get("src")
        summary = _clean(node.get("field_abstract"))
        out.append(RawEvent(
            source="gradireland",
            source_id=str(node.get("nid") or node.get("uuid")),
            url=GRADIRELAND + alias,
            title=_clean(node.get("title"), 300),
            summary=summary,
            starts_at=starts, ends_at=ends if ends and ends >= starts else None, has_time=has_time,
            organizer="gradireland",
            image_url=GRADIRELAND + src if src else None,
            # The listing names no venue; the county comes from its own words, and only
            # when they name one (the roadshow names eight campuses, so it has none).
            region=_only_county(summary + " " + _clean(body, 3000)),
        ))
    return out


def gradireland(client: httpx.Client) -> list[RawEvent]:
    text = _get(client, f"{GRADIRELAND}/page-data/events/page-data.json")
    return parse_gradireland(text) if text else []


READERS = {"eventbrite": eventbrite, "meetup": meetup, "luma": luma, "gradireland": gradireland}
