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


def test_an_event_a_newer_rule_rules_out_is_hidden_not_deleted(session):
    from datetime import datetime, timezone

    session.add(Event(source="meetup", source_id="x", url="https://x", title="After-Work Quick Date Rounds",
                      starts_at=datetime(2026, 10, 17, tzinfo=timezone.utc), kind="tech"))
    session.flush()
    crawl.store(session, [], crawl.Summary())
    hidden = session.execute(select(Event)).scalar_one()
    assert hidden.cancelled


def _event(**kw):
    base = dict(id=1, source="eventbrite", source_id="1", url="https://www.eventbrite.ie/e/1",
                title="Job Fair", kind="fair", has_time=True, is_online=False, cancelled=False,
                starts_at=datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), ends_at=None,
                region="Dublin", town="Dublin 15", venue="Hall", summary="", organizer=None,
                is_free=None, image_url=None)
    base.update(kw)
    return Event(**base)


def test_an_event_moves_from_ahead_to_on_now_to_completed_by_the_clock():
    from jobfinder.events import listing

    event = _event()   # 10:00 to 13:00 Irish time, the end assumed
    starts, ends = listing.span(event)
    assert (starts.hour, ends.hour) == (10, 13)
    status = lambda when: listing.status_of(starts, ends, when)
    assert status(datetime(2026, 10, 1, tzinfo=timezone.utc)) == "later"
    assert status(datetime(2026, 10, 12, tzinfo=timezone.utc)) == "week"
    assert status(datetime(2026, 10, 15, 10, tzinfo=timezone.utc)) == "live"
    assert status(datetime(2026, 10, 15, 13, tzinfo=timezone.utc)) == "done"
    view = listing.EventView(event, starts, ends, "week")
    assert view.countdown(datetime(2026, 10, 13, 8, tzinfo=timezone.utc)) == "In 2 days"
    assert view.time_label == "From 10:00" and view.where == "Hall, Dublin 15"


def test_a_date_with_no_hour_runs_all_day_and_a_run_of_days_says_so():
    from jobfinder.events import listing

    day = _event(has_time=False, starts_at=datetime(2026, 10, 14, 23, 0, tzinfo=timezone.utc))
    starts, ends = listing.span(day)
    assert listing.EventView(day, starts, ends, "week").time_label == "All day"
    run = _event(has_time=False, starts_at=datetime(2026, 9, 21, 23, 0, tzinfo=timezone.utc),
                 ends_at=datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc))
    starts, ends = listing.span(run)
    assert listing.EventView(run, starts, ends, "live").time_label == "22 Sep to 20 Oct"


def test_the_calendar_file_is_valid_and_folded():
    from jobfinder.events import listing

    event = _event(summary="A long description " * 10)
    starts, ends = listing.span(event)
    text = listing.ics(listing.EventView(event, starts, ends, "week"), "https://sorted-place.vercel.app/events/1")
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR" and "DTSTART:20261015T090000Z" in lines
    assert all(len(line.encode()) <= 75 for line in lines)
    assert "UID:eventbrite-1@sorted-place" in lines


def test_the_calendar_marks_days_with_events():
    from datetime import date

    from jobfinder.events import listing

    event = _event()
    starts, ends = listing.span(event)
    months = listing.calendar([listing.EventView(event, starts, ends, "week")], date(2026, 10, 10), count=2)
    october = months[0]
    assert october["label"] == "October 2026" and october["lead"] == 3   # 1 October 2026 is a Thursday
    fifteenth = october["days"][14]
    assert fifteenth["n"] == 1 and october["days"][9]["today"]
    assert months[1]["label"] == "November 2026"
