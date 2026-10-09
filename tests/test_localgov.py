"""localgovernmentjobs.ie: every council's vacancies from one result list."""

from __future__ import annotations

import httpx
import respx

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.boards import localgov
from jobfinder.sources.boards.localgov import (
    LocalGovernmentAdapter,
    authority_place,
    council_from_path,
    parse_cards,
)


def _card(path: str, title: str, council: str, location: str = "") -> str:
    return (
        f'<div class="job-cards-item"><a href="{path}" title="Link to {title} Job">'
        '<div class="job-cards-item-content"><div class="job-cards-council">'
        f"<h2>{title}</h2><p>{council}</p></div>"
        '<div class="job-cards-date"><p>7 Days remaining</p>'
        '<p><span class="bold">Advertised Date: </span>25 Sep 2026</p>'
        '<p><span class="bold">Closing Date: </span>16 Oct 2026, 12:00 AM</p>'
        '<p><span class="bold">Grade: </span>Library Assistant</p>'
        f'<p><span class="bold">Location: </span>{location}</p>'
        "</div></div></a></div>"
    )


LIST = '<div class="job-cards-items">' + "".join([
    _card("/jobs/Meath-County-Council/Library-Assistant", "Library Assistant", "Meath County Council"),
    _card("/jobs/Archived-Monaghan-County-Council-Jobs/Assistant-Engineer-2026",
          "Assistant Engineer", "", "Monaghan"),
    _card("/jobs/Fingal-County-Council/Lifeguard", "Seasonal Lifeguard", "Fingal County Council"),
]) + "</div>"

ADVERT = (
    '<div class="job-info-content"><div class="col-2">'
    '<div class="job-info-salary">Hourly Rate</div><div class="job-info-salary">Meath</div>'
    '<div class="job-info-position">Part-time</div>'
    '<div class="job-info-expiry">Closing Date: 14 Oct 2026</div></div></div>'
    '<div class="job-description-copy"><p>Meath County Council invites applications.</p></div>'
)


def test_cards_name_their_council_and_place() -> None:
    jobs = parse_cards(LIST)
    assert [(j.title, j.company_name, j.location_raw) for j in jobs] == [
        ("Library Assistant", "Meath County Council", "Meath, Ireland"),
        # An open advert filed under an "Archived" folder prints no council.
        ("Assistant Engineer", "Monaghan County Council", "Monaghan, Ireland"),
        ("Seasonal Lifeguard", "Fingal County Council", "Dublin, Ireland"),
    ]
    assert jobs[0].url == "https://www.localgovernmentjobs.ie/jobs/Meath-County-Council/Library-Assistant"
    assert jobs[0].posted_at.day == 25 and jobs[0].posted_at.month == 9


def test_every_authority_resolves_to_an_irish_place() -> None:
    for council in ("Dun Laoghaire Rathdown County Council", "South Dublin County Council",
                    "Limerick City and County Council", "Galway City Council",
                    "Local Government Management Agency"):
        assert normalize_location(authority_place(council)).is_ireland, council
    assert council_from_path("/jobs/Archived-Dublin-City-Council-Jobs/x") == "Dublin City Council"


@respx.mock
def test_each_advert_adds_its_county_contract_and_text() -> None:
    respx.get(url__startswith=localgov.ORIGIN + "/Search/").mock(return_value=httpx.Response(200, text=LIST))
    respx.get(url__startswith=localgov.ORIGIN + "/jobs/Meath").mock(return_value=httpx.Response(200, text=ADVERT))
    respx.get(url__startswith=localgov.ORIGIN + "/jobs/").mock(return_value=httpx.Response(500))

    result = LocalGovernmentAdapter().fetch("localgovernmentjobs.ie")

    assert result.ok and len(result.jobs) == 3
    library = result.jobs[0]
    assert library.employment_type == "Part-time"
    assert "invites applications" in library.description
    # An advert that would not load keeps its card.
    assert result.jobs[2].title == "Seasonal Lifeguard" and result.jobs[2].employment_type is None
