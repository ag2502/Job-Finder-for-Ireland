"""Adapter interface shared by every job source.

The important design decision here is that `fetch()` **returns a status instead of
raising**. Downstream reconciliation has to distinguish "this source was reached and the
job is genuinely gone" from "this source could not be reached", because closing jobs on
the strength of a failed request would silently empty the database during any upstream
outage. Making that distinction part of the return type means an adapter author cannot
accidentally omit it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from jobfinder.core.config import settings
from jobfinder.core.models import CrawlStatus

logger = logging.getLogger(__name__)


@dataclass
class RawJob:
    """One posting as the source described it, before normalization."""

    source_job_id: str
    title: str
    url: str
    location_raw: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    department: str | None = None

    # Some boards list a role against several offices. Each extra location is
    # expanded into its own candidate so a Dublin secondary location is not lost.
    extra_locations: list[str] = field(default_factory=list)


@dataclass
class FetchResult:
    status: CrawlStatus
    jobs: list[RawJob] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status is CrawlStatus.OK

    @classmethod
    def failed(cls, error: str) -> FetchResult:
        return cls(status=CrawlStatus.FAILED, error=error)


def build_client(**kwargs) -> httpx.Client:
    """HTTP client carrying the crawler's identifying User-Agent."""
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    }
    headers.update(kwargs.pop("headers", {}))
    return httpx.Client(
        headers=headers,
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        **kwargs,
    )


class BaseAdapter:
    """Base class handling error containment, timing and politeness.

    Subclasses implement `_fetch`, which may raise freely; the wrapper converts any
    exception into a FAILED result so one broken adapter can never abort a crawl or
    endanger another source's jobs.
    """

    name: str = "base"
    tier: int = 1

    def fetch(self, slug: str, client: httpx.Client | None = None) -> FetchResult:
        started = time.monotonic()
        owns_client = client is None
        client = client or build_client()
        try:
            jobs = self._fetch(slug, client)
        except httpx.HTTPStatusError as exc:
            return self._fail(
                f"HTTP {exc.response.status_code} from {exc.request.url}", started
            )
        except httpx.RequestError as exc:
            return self._fail(f"request error: {exc!r}", started)
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see docstring
            logger.exception("adapter %s failed on slug %s", self.name, slug)
            return self._fail(f"{type(exc).__name__}: {exc}", started)
        finally:
            if owns_client:
                client.close()

        return FetchResult(
            status=CrawlStatus.OK,
            jobs=jobs,
            duration_seconds=time.monotonic() - started,
        )

    def _fail(self, error: str, started: float) -> FetchResult:
        logger.warning("%s: %s", self.name, error)
        return FetchResult(
            status=CrawlStatus.FAILED,
            error=error,
            duration_seconds=time.monotonic() - started,
        )

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        raise NotImplementedError

    @staticmethod
    def polite_pause() -> None:
        if settings.request_delay_seconds > 0:
            time.sleep(settings.request_delay_seconds)


_REGISTRY: dict[str, BaseAdapter] = {}


def register(adapter: BaseAdapter) -> BaseAdapter:
    _REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> BaseAdapter | None:
    return _REGISTRY.get(name)


def all_adapters() -> dict[str, BaseAdapter]:
    return dict(_REGISTRY)
