"""People First job boards: a JSON API that wants the tenant in a header."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.peoplefirst import PeopleFirstAdapter

BASE = "https://acme.jobs.people-first.com/api/v1"


@respx.mock
def test_the_board_lists_its_jobs_with_their_adverts(monkeypatch) -> None:
    monkeypatch.setattr(PeopleFirstAdapter, "polite_pause", staticmethod(lambda: None))
    profile = respx.get(f"{BASE}/recruitment/jobboard/profile").mock(return_value=httpx.Response(200, json={
        "data": {"jobBoardProfile": {"_links": {"availableJobs": {"href": "recruitment/org/Acme/availablejobs"}}}}}))
    respx.get(f"{BASE}/recruitment/org/Acme/availablejobs").mock(return_value=httpx.Response(200, json={
        "data": {"availablejobs": [
            {"jobId": "j1", "title": "Support Worker Part Time", "formattedAddress": "Wicklow- WCC\r\nWicklow\r\nIreland",
             "startDate": "2026-06-29"},
            {"jobId": "j2", "title": "Internal Role", "internalOnly": True},
        ]}, "meta": {"totalPages": 1}}))
    respx.get(f"{BASE}/recruitment/jobdetails/j1").mock(return_value=httpx.Response(200, json={
        "data": {"jobDetails": {"jobDescription": "<p>20 hours per week.</p>"}}}))

    result = PeopleFirstAdapter().fetch("acme")

    assert result.status is CrawlStatus.OK
    assert [(j.title, j.location_raw) for j in result.jobs] == [("Support Worker Part Time", "Wicklow, Ireland")]
    assert result.jobs[0].description == "<p>20 hours per week.</p>"
    assert result.jobs[0].url.endswith("/jobs/details/recruitment%2Fjobdetails%2Fj1")
    assert profile.calls[0].request.headers["tenantcode"] == "acme"
