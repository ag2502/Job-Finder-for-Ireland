"""HealthBox HR job feeds, at app.hbhr.io/jobs-feed/web/company/{id}.

HealthBox HR is an Irish HR and recruitment system used by creches and care providers.
An employer's feed page is server-rendered and lists every open vacancy as a card:
the title, pay, the hours ("Full Time, Part Time, Permanent") and the advert text.
robots.txt closes the application pages the cards link to, and those are never fetched;
a searcher is sent to them to apply.

Cards name no place, so the slug may carry one for an employer that hires in one
region: ``{company id}|Kildare, Ireland``.
"""

from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, RawJob, register

FEED = "https://app.hbhr.io/jobs-feed/web/company/{id}"
APPLY = "/job-application/external/"


def parse_feed(html: str, place: str | None = None) -> list[RawJob]:
    jobs: list[RawJob] = []
    for card in HTMLParser(html).css("div.card"):
        link = card.css_first(f'a[href*="{APPLY}"]')
        if link is None:
            continue
        url = link.attributes.get("href") or ""
        title = link.text(strip=True)
        if not url or not title:
            continue
        body = card.css_first(".card-body")
        badges = [b.text(separator=" ", strip=True) for b in body.css("span.badge")] if body else []
        hours = next((b for b in badges if any(w in b.lower() for w in ("time", "permanent", "temporary", "contract"))), None)
        # The title often names the town ("New Creche Opening in Leixlip"): its county
        # first, the slug's place otherwise.
        location = place
        if place:
            region = normalize_location(f"{title}, {place}").region
            location = f"{region}, Ireland" if region else place
        jobs.append(RawJob(
            source_job_id=url.rstrip("/").rsplit("/", 1)[-1],
            title=title,
            url=url,
            location_raw=location,
            description=body.html if body is not None else None,
            employment_type=hours,
        ))
    return jobs


class HealthBoxAdapter(BaseAdapter):
    name = "healthbox"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        company, _, place = slug.partition("|")
        response = client.get(FEED.format(id=company.strip()))
        response.raise_for_status()
        if "Job Vacancies" not in response.text and APPLY not in response.text:
            raise ValueError(f"HealthBox feed {company!r} has no vacancy list")
        return parse_feed(response.text, place.strip() or None)


register(HealthBoxAdapter())
