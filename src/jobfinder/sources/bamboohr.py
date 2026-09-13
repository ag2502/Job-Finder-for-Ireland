"""BambooHR careers adapter.

    https://{slug}.bamboohr.com/careers/list

BambooHR is common among Irish SMEs — Integrity360, Ocuco, Oneview, Openjaw and Ding
among them — and every tenant serves the same unauthenticated JSON list. The list
carries no description or posting date; those live on per-job pages, and one request per
board is worth far more than full adverts at one request per job.

An unknown tenant does not 404: BambooHR redirects it to its own marketing site, which
serves HTML. That is caught as unparseable JSON, so the base adapter reports FAILED
rather than an empty board that would close every job.
"""

from __future__ import annotations

import httpx

from jobfinder.sources.base import BaseAdapter, RawJob, register


def _location(job: dict) -> str | None:
    remote = bool(job.get("isRemote"))

    parts: list[str] = []
    for block in (job.get("location") or {}, job.get("atsLocation") or {}):
        for key in ("city", "state", "province", "country"):
            value = block.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in parts:
                parts.append(value.strip())

    if parts:
        return ", ".join(parts) + (" (Remote)" if remote else "")
    return "Remote" if remote else None


class BambooHRAdapter(BaseAdapter):
    name = "bamboohr"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        response = client.get(f"https://{slug}.bamboohr.com/careers/list")
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            raise ValueError(f"unexpected BambooHR payload for {slug!r}")

        jobs: list[RawJob] = []
        for job in payload["result"]:
            job_id = str(job.get("id") or "").strip()
            title = (job.get("jobOpeningName") or "").strip()
            if not job_id or not title:
                continue
            jobs.append(
                RawJob(
                    source_job_id=job_id,
                    title=title,
                    url=f"https://{slug}.bamboohr.com/careers/{job_id}",
                    location_raw=_location(job),
                    department=job.get("departmentLabel") or None,
                )
            )
        return jobs


register(BambooHRAdapter())
