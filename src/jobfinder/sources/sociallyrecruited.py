"""SociallyRecruited career sites, such as DFS's yourcareer.dfscareers.co.uk.

The search form filters by country only when it is posted (`country=106` is the
Republic of Ireland on DFS's site); the same search by GET ignores it and lists the
whole UK board. So the search is posted with the country, and each job page it links
to is read for its JobPosting markup. robots.txt asks for ten seconds between requests,
which is honoured.

The slug is ``host|country id``.
"""

from __future__ import annotations

import re
import time

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy, iter_ld_objects, parse_job_posting

JOB_LINK = re.compile(r'href="(?:https?://[^"/]+)?(/jobs/job/[^"/]+/\d+)"')
# Past this the search is a different board.
MAX_JOBS = 200


class SociallyRecruitedAdapter(BaseAdapter):
    name = "sociallyrecruited"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, _, country = slug.partition("|")
        origin = f"https://{host.strip()}"
        robots = RobotsPolicy(origin, client)
        if not robots.allows(f"{origin}/jobs/search"):
            raise PermissionError(f"robots.txt disallows {origin}/jobs/search")
        delay = max(robots.crawl_delay(), 1.0)

        search = client.post(
            f"{origin}/jobs/search",
            data={"keywords": "", "country": country.strip(), "location": "", "contract": "", "category": ""},
        )
        search.raise_for_status()
        paths = list(dict.fromkeys(JOB_LINK.findall(search.text)))[:MAX_JOBS]
        if not paths and "search" not in search.text.lower():
            raise ValueError(f"{origin} returned no search page")

        jobs: list[RawJob] = []
        for path in paths:
            time.sleep(delay)
            page = client.get(origin + path)
            if page.status_code != 200:
                continue
            for obj in iter_ld_objects(page.text):
                if obj.get("@type") == "JobPosting":
                    job = parse_job_posting(obj, origin + path)
                    if job:
                        job.source_job_id = path.rsplit("/", 1)[-1]
                        jobs.append(job)
                    break
        return jobs


register(SociallyRecruitedAdapter())
