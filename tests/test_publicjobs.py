"""publicjobs.ie: the results page is read directly, since the board's feed is off."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import CrawlStatus, Source
from jobfinder.sources.boards import publicjobs
from jobfinder.sources.boards.publicjobs import PublicJobsAdapter, parse_results

OPP = (
    "https://publicjobs.tal.net/vx/lang-en-GB/mobile-0/appcentre-1/brand-4/"
    "xf-c564c93d63b4/candidate/so/pm/1/pl/3/opp/{id}-{slug}/en-GB"
)


def _row(opp_id: int, title: str, org: str, location: str) -> str:
    return (
        f'<div class="opp_{opp_id} search_res details_row candidate-opp-tile">'
        f'<a href="{OPP.format(id=opp_id, slug=title.replace(" ", "-"))}">{title}</a>'
        "<div><span>Vacancy type:</span><span>publicjobs</span></div>"
        f"<div><span>Department/Organisation:</span><span>{org}</span></div>"
        f"<div><span>Location:</span><span>{location}</span></div>"
        "<div><span>Advertising Date:</span><span>18 Sept 2026</span></div>"
        "<div><span>Closing Date:</span><span>8 Oct 2026</span></div>"
        "</div>"
    )


def _page(total: int, *rows: str) -> str:
    facets = '<span class="facet_count">(18 Results)</span>'
    return f"<html><body>{facets}<h2>{total} results match</h2>{''.join(rows)}</body></html>"


def test_each_result_is_filed_under_its_own_public_body():
    jobs, total = parse_results(
        _page(
            2,
            _row(8877, "Head of Corporate Systems", "An Garda Síochána", "Dublin"),
            _row(8026, "Senior Executive Planner", "Fingal County Council", "Fingal County Council"),
        )
    )

    assert total == 2  # the headline count, not a facet's
    assert [(j.source_job_id, j.company_name, j.location_raw) for j in jobs] == [
        ("8877", "An Garda Síochána", "Dublin, Ireland"),
        ("8026", "Fingal County Council", "Fingal County Council, Ireland"),
    ]
    assert jobs[0].posted_at.day == 18
    assert "Closing Date: 8 Oct 2026" in jobs[0].description


@respx.mock
def test_every_page_of_a_board_is_read(monkeypatch):
    monkeypatch.setattr(publicjobs, "CRAWL_DELAY_SECONDS", 0)
    monkeypatch.setattr(publicjobs, "PAGE_SIZE", 1)
    respx.get("https://publicjobs.tal.net/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /*/agent/\n")
    )
    board = respx.get(url__startswith="https://publicjobs.tal.net/vx/")
    board.side_effect = [
        httpx.Response(200, html=_page(2, _row(1, "Clerical Officer", "Revenue", "Dublin"))),
        httpx.Response(200, html=_page(2, _row(2, "Executive Officer", "CSO", "Cork"))),
    ]

    result = PublicJobsAdapter().fetch("3")

    assert result.status is CrawlStatus.OK
    assert sorted(j.source_job_id for j in result.jobs) == ["1", "2"]


def test_fingal_is_dublin():
    from jobfinder.normalize.location import normalize_location

    assert normalize_location("Fingal County Council, Ireland").is_dublin


def test_seeded_sources_take_their_adapters_tier(session: Session, tmp_path):
    from jobfinder.registry.seed import seed_companies

    seed = tmp_path / "seed.csv"
    seed.write_text(
        "name,adapter,slug,coverage_priority,is_public_listed,seed_source,notes,careers_url\n"
        "Public Appointments Service,publicjobs,3,1,false,board,,\n"
    )
    seed_companies(session, seed)

    source = session.execute(select(Source)).scalar_one()
    assert (source.adapter, source.tier) == ("publicjobs", 4)
