"""Careers events: reading the listing sites, deciding what is a careers event, and
keeping the table the way jobs are kept, added to and refreshed but never emptied."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from jobfinder.core.models import Event
from jobfinder.events import crawl, relevance, sources


def _eventbrite_page(results: list[dict], pages: int = 1) -> str:
    data = {"search_data": {"events": {"results": results, "promoted_results": [{"name": "Ad"}],
                                       "pagination": {"page_count": pages}}}}
    return f"<html><script>window.__SERVER_DATA__ = {json.dumps(data)};</script></html>"


FAIR = {
    "eid": "2000250418848", "name": "Multi Sector Job Fair - Liffey Valley",
    "url": "https://www.eventbrite.ie/e/multi-sector-job-fair-tickets-2000250418848?aff=x",
    "summary": "Join us and explore job and training opportunities",
    "start_date": "2026-11-05", "start_time": "10:00", "end_date": "2026-11-05",
    "end_time": "12:30", "timezone": "Europe/Dublin", "is_online_event": False,
    "primary_venue": {"name": "Liffey Valley Shopping Centre",
                      "address": {"city": "Dublin 22", "region": "Dublin", "country": "IE"}},
    "tags": [{"prefix": "EventbriteSubCategory", "display_name": "Career"},
             {"prefix": "OrganizerTag", "display_name": "job_fair_2024"}],
}


def test_an_eventbrite_search_page_reads_into_events():
    events, pages = sources.parse_eventbrite(_eventbrite_page([FAIR], pages=4))
    assert pages == 4 and len(events) == 1
    fair = events[0]
    assert fair.source_id == "2000250418848"
    assert fair.url == "https://www.eventbrite.ie/e/multi-sector-job-fair-tickets-2000250418848"
    # 10:00 in Dublin in November is 10:00 UTC; the time zone is honoured either way.
    assert fair.starts_at == datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    assert fair.ends_at == datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)
    assert fair.region == "Dublin" and fair.tags == ("Career",)
    assert crawl.keep(fair) == "fair"


def test_an_eventbrite_venue_outside_ireland_is_left_out():
    abroad = dict(FAIR, eid="1", primary_venue={"name": "Hall", "address": {"city": "Belfast", "country": "GB"}})
    assert sources.parse_eventbrite(_eventbrite_page([abroad]))[0] == []


def test_schema_org_events_read_from_a_city_page():
    page = """<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
      {"item":{"@type":"Event","url":"https://www.meetup.com/awsdublin/events/311/","name":"AWS UG Dublin",
        "startDate":"2026-10-27T17:45:00.000Z","location":{"@type":"Place","name":"Workday",
        "address":{"addressLocality":"Dublin","addressCountry":"ie"}},"organizer":{"name":"AWS UG"}}},
      {"item":{"@type":"Event","url":"https://www.meetup.com/owasp-italy/events/9/","name":"OWASP Italy Online",
        "startDate":"2026-10-16T14:00:00.000Z","location":{"@type":"VirtualLocation"}}}]}</script>"""
    events = sources.parse_jsonld(page, "meetup", tech_hint=True, in_person_only=True)
    assert [e.title for e in events] == ["AWS UG Dublin"]
    assert events[0].source_id == "awsdublin/events/311" and events[0].region == "Dublin"
    assert crawl.keep(events[0]) == "tech"


def test_gradireland_events_read_from_its_page_data():
    data = {"result": {"data": {"allOrphanEventChildren": {"nodes": [
        {"title": "Engineer Your Career", "nid": "237695", "path": {"alias": "/events/engineer-your-career"},
         "field_abstract": "Meet engineers", "field_event_start_date": "2026-10-15",
         "field_event_start_time_info": "10:30 AM", "field_event_end_date": "2026-10-15",
         "field_event_end_time_info": "5:00 PM",
         "relationships": {"field_content": [{"field_body": {"processed": "<p>Join gradireland in Dublin</p>"}}]}},
        {"title": "gradireland Roadshow", "nid": "245011", "path": {"alias": "/events/gradireland-roadshow"},
         "field_abstract": "Galway, Limerick, Dublin and Cork", "field_event_start_date": "2026-09-22",
         "field_event_end_date": "2026-10-20"},
    ]}}}}
    career, roadshow = sources.parse_gradireland(json.dumps(data))
    assert career.url == "https://gradireland.com/events/engineer-your-career"
    assert career.starts_at == datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)   # Irish summer time
    assert career.region == "Dublin" and crawl.keep(career) == "student"
    # Several counties named means no one county, and a date with no hour is all day.
    assert roadshow.region is None and not roadshow.has_time
    assert roadshow.ends_at == datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)


def test_only_careers_events_are_kept():
    keep = relevance.is_careers_event
    assert keep("Job and Training Fair - Tallaght")
    assert keep("Some Expo", career_tag=True)
    assert not keep("Tech and Business Networking | Elevating Your Potential")
    assert not keep("Dublin Social Singles Mixer 21+", tech_hint=True)
    assert not keep("The Graduates - live at The Cat & Cage Folk Club")
    assert not keep("Dún Laoghaire After-Work Quick Date Rounds for Young Professionals", tech_hint=True)
    assert keep("Dub|Sec October Meetup", tech_hint=True)
    assert not keep("Dub|Sec October Meetup")     # a tech word alone, from a general listing


def test_each_event_files_under_one_kind():
    kind = relevance.kind_of
    assert kind("Work and Skills Job and Training Fair for People with Disabilities") == "inclusive"
    assert kind("ATU Donegal Careers Fair 2026") == "student"
    assert kind("Recruitment Event with Chemist Warehouse") == "fair"
    assert kind("How to Write and Refresh Your CV workshop") == "skills"
    assert kind("AWS UG Dublin", tech_hint=True) == "tech"
    assert set(relevance.KINDS) == {"fair", "student", "inclusive", "tech", "skills", "networking"}


def test_a_crawl_adds_refreshes_and_never_removes(session):
    fair, _ = sources.parse_eventbrite(_eventbrite_page([FAIR]))
    noise = sources.RawEvent(source="eventbrite", source_id="9", url="https://x", title="Wine Tasting",
                             starts_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    first = crawl.Summary()
    crawl.store(session, fair + [noise], first)
    assert (first.kept, first.added) == (1, 1)

    # Next run: the time moved, and the listing is the only one seen.
    moved = sources.parse_eventbrite(_eventbrite_page([dict(FAIR, start_time="11:00")]))[0]
    second = crawl.Summary()
    crawl.store(session, moved, second)
    assert (second.added, second.updated) == (0, 1)
    stored = session.execute(select(Event)).scalars().all()
    assert len(stored) == 1 and stored[0].starts_at.hour == 11

    # A run that finds nothing, or a site that is down, leaves every event where it was.
    crawl.store(session, [], crawl.Summary())
    assert len(session.execute(select(Event)).scalars().all()) == 1


def test_one_site_failing_does_not_stop_the_others(session):
    def broken(client):
        raise RuntimeError("down")

    def working(client):
        return sources.parse_eventbrite(_eventbrite_page([FAIR]))[0]

    summary = crawl.run(session, readers={"meetup": broken, "eventbrite": working})
    assert summary.failed == ["meetup"] and summary.added == 1


def test_clock_times_in_every_shape_listings_use():
    assert sources._clock("18:30") == (18, 30)
    assert sources._clock("6:30 PM") == (18, 30)
    assert sources._clock("10am") == (10, 0)
    assert sources._clock("12:00 AM") == (0, 0)
    assert sources._clock("TBC") is None and sources._clock(None) is None
