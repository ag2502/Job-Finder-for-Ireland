"""SociallyRecruited: the country filter applies only to a posted search."""

from __future__ import annotations

import httpx
import respx

from jobfinder.sources import sociallyrecruited
from jobfinder.sources.sociallyrecruited import SociallyRecruitedAdapter

ORIGIN = "https://yourcareer.dfscareers.co.uk"
JOB = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Retail Sales Advisor - 20 Hours",'
       ' "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Carrickmines",'
       ' "addressCountry": "Ireland"}}}</script>')


@respx.mock
def test_the_country_search_is_posted_and_each_job_page_read(monkeypatch):
    monkeypatch.setattr(sociallyrecruited.time, "sleep", lambda seconds: None)
    respx.get(f"{ORIGIN}/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 10\n"))
    search = respx.post(f"{ORIGIN}/jobs/search").mock(return_value=httpx.Response(
        200, text='<a href="/jobs/job/Retail-Sales-Advisor-20-Hours/761">x</a> search'))
    respx.get(f"{ORIGIN}/jobs/job/Retail-Sales-Advisor-20-Hours/761").mock(return_value=httpx.Response(200, text=JOB))

    jobs = SociallyRecruitedAdapter().fetch("yourcareer.dfscareers.co.uk|106").jobs

    assert b"country=106" in search.calls[0].request.content
    assert [(j.source_job_id, j.title) for j in jobs] == [("761", "Retail Sales Advisor - 20 Hours")]
    assert "Carrickmines" in jobs[0].location_raw
