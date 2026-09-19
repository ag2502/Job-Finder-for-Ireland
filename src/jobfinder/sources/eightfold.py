"""Eightfold AI careers-site adapter.

    https://{tenant}.eightfold.ai/api/pcsx/search?domain={domain}&location=Ireland&start={n}
    https://{tenant}.eightfold.ai/api/pcsx/position_details?position_id={id}&domain={domain}

Eightfold runs the careers sites of several large Dublin employers — PayPal, Qualcomm,
Morgan Stanley, Boston Scientific, HP. Tenants have moved to its "PCS X" site, whose
search API is public and filters by location natively, so only Irish roles are fetched:
a multinational's board is thousands of postings, and the site serves Dublin.

The API is addressed by the tenant *and* the company's email domain. Detection finds the
tenant; the domain is read from the careers page, which states it in its own links. The
slug is ``tenant`` or ``tenant|domain``.

Descriptions come from a per-position endpoint and are capped, like every adapter that
needs a request per posting.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

COUNTRY = "Ireland"
MAX_PAGES = 30
MAX_DESCRIPTIONS = 60
DOMAIN_IN_PAGE = re.compile(r"domain=([a-z0-9.-]+\.[a-z]{2,})", re.I)

# Eightfold's own test tenants look like customers to a fingerprint.
NOT_A_CUSTOMER = re.compile(r"sandbox|demo|staging|test", re.I)


def split_slug(slug: str) -> tuple[str, str | None]:
    tenant, _, domain = slug.partition("|")
    return tenant, domain or None


def resolve_domain(tenant: str, client: httpx.Client) -> str:
    response = client.get(f"https://{tenant}.eightfold.ai/careers")
    response.raise_for_status()
    found = DOMAIN_IN_PAGE.findall(response.text)
    if not found:
        # Some sites never state it; the tenant is the company's name, and the search
        # rejects a wrong domain rather than answering for someone else.
        return f"{tenant}.com"
    # The page's own domain is the one it repeats; a partner's appears once in a footer.
    return max(set(found), key=found.count)


def _epoch(value) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


class EightfoldAdapter(BaseAdapter):
    name = "eightfold"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, domain = split_slug(slug)
        if NOT_A_CUSTOMER.search(tenant):
            raise ValueError(f"{tenant!r} is an Eightfold test tenant, not an employer")
        domain = domain or resolve_domain(tenant, client)
        base = f"https://{tenant}.eightfold.ai"

        positions: list[dict] = []
        truncated = False
        for page in range(MAX_PAGES):
            response = client.get(
                f"{base}/api/pcsx/search",
                params={"domain": domain, "location": COUNTRY, "start": len(positions)},
            )
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            batch = data.get("positions") or []
            positions.extend(batch)
            if not batch or len(positions) >= (data.get("count") or 0):
                break
            self.polite_pause()
        else:
            truncated = True

        jobs: list[RawJob] = []
        for index, item in enumerate(positions):
            job_id = item.get("id")
            if not job_id:
                continue
            locations = [loc for loc in item.get("locations") or [] if loc]
            description = (
                self._description(base, domain, job_id, client)
                if index < MAX_DESCRIPTIONS
                else None
            )
            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=item.get("name") or "",
                    url=f"{base}{item.get('positionUrl') or f'/careers/job/{job_id}'}",
                    location_raw=locations[0] if locations else None,
                    description=description,
                    posted_at=_epoch(item.get("postedTs")),
                    department=item.get("department"),
                    extra_locations=locations[1:],
                )
            )
        return PartialJobs(jobs) if truncated else jobs

    def _description(self, base: str, domain: str, job_id, client: httpx.Client) -> str | None:
        try:
            response = client.get(
                f"{base}/api/pcsx/position_details",
                params={"position_id": job_id, "domain": domain},
            )
            response.raise_for_status()
            return ((response.json() or {}).get("data") or {}).get("jobDescription")
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("eightfold: no description for %s: %r", job_id, exc)
            return None
        finally:
            self.polite_pause()


register(EightfoldAdapter())
