"""HR Cloud job board adapter (CoreHR's newer boards run on it).

    GET https://{host}/api/job/GetJobOpenings?tenantCode={TENANT}&boardName=&page={n}&searchTerm=
    GET https://{host}/api/job/GetJobDetail?tenantCode={TENANT}&jobId={id}

A board lives at `https://{tenant}.corehr.hrcloud.hr/{tenant}/#/jobs` and is drawn in
the browser from these two public endpoints. The list gives ten openings a page with the
board's `TotalCount` and a short location ("Limerick, Ireland"); the detail call adds the
advert, the full address and the publish date, so each opening is read once more for them.
An opening's own page is `https://{host}/{tenant}/job/{id}`.

The slug is the host (`hmveng.corehr.hrcloud.hr`). The tenant code is its first label,
upper-cased, unless the slug names it: `host|CODE`.
"""

from __future__ import annotations

import httpx
from dateutil import parser as dateparser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

MAX_PAGES = 30
# A read short of this share of the stated total is refused rather than reconciled, so a
# board that broke off mid-read never closes the openings it did not get to.
COMPLETENESS = 0.9


def split_slug(slug: str) -> tuple[str, str]:
    host, _, code = slug.partition("|")
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    return host, (code or host.split(".")[0]).upper()


class HrCloudAdapter(BaseAdapter):
    name = "hrcloud"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, code = split_slug(slug)
        openings: dict[str, dict] = {}
        total: int | None = None
        partial = False

        for page in range(1, MAX_PAGES + 1):
            response = client.get(
                f"https://{host}/api/job/GetJobOpenings",
                params={"tenantCode": code, "boardName": "", "page": page, "searchTerm": ""},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "FoundJobs" not in payload:
                raise ValueError(f"unexpected HR Cloud payload for {slug!r}")
            if total is None:
                total = int(payload.get("TotalCount") or 0)
            rows = payload.get("FoundJobs") or []
            for row in rows:
                if row.get("Id") and row.get("JobTitle"):
                    openings.setdefault(str(row["Id"]), row)
            if not rows or len(openings) >= total or page >= int(payload.get("TotalPages") or 0):
                break
            self.polite_pause()
        else:
            # Past the page ceiling: what was read is kept, but never used to close.
            partial = True

        if total and len(openings) < total * COMPLETENESS:
            raise ValueError(
                f"HR Cloud read {len(openings)} of {total} openings from {host}; "
                "refusing to report an incomplete board"
            )

        jobs = [self._job(host, code, row, client) for row in openings.values()]
        return PartialJobs(jobs) if partial else jobs

    def _job(self, host: str, code: str, row: dict, client: httpx.Client) -> RawJob:
        job_id = str(row["Id"])
        detail: dict = {}
        try:
            response = client.get(
                f"https://{host}/api/job/GetJobDetail",
                params={"tenantCode": code, "jobId": job_id, "referrerSource": ""},
            )
            response.raise_for_status()
            detail = response.json().get("JobDetailDto") or {}
        except (httpx.HTTPError, ValueError):
            # The list alone still names the role and where it is; the advert is a bonus.
            detail = {}
        self.polite_pause()

        posted = detail.get("LastPublishedDate") or detail.get("CreatedOn")
        try:
            posted_at = dateparser.isoparse(posted) if posted else None
        except (ValueError, OverflowError):
            posted_at = None
        return RawJob(
            source_job_id=job_id,
            title=(row.get("JobTitle") or "").strip(),
            url=f"https://{host}/{code.lower()}/job/{job_id}",
            location_raw=detail.get("Location") or row.get("Location"),
            description=detail.get("Description"),
            posted_at=posted_at,
            department=row.get("Department"),
        )


register(HrCloudAdapter())
