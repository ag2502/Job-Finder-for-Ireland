"""Concurrent fetching, with politeness enforced per host.

At 39 sources a serial crawl is fine. At several thousand it is not: one source at one
second of latency plus a one-second courtesy pause is roughly two hours per thousand
sources, and the GitHub Actions job that runs the crawl is capped at six.

The parallelism is deliberately confined to the network. Fetching happens across a
thread pool; reconciliation stays on the caller's thread, one source at a time, because
that is where the project's correctness guarantees live — a SQLAlchemy Session is not
thread-safe, and the close-on-confirmed-absence logic reads and writes state that must
not interleave. `fetch_all` therefore yields completed results for the caller to
reconcile serially, rather than touching the database itself.

**Concurrency is per host, not global.** Twenty Greenhouse boards are twenty slugs on
one hostname, so a naive pool of sixteen workers would put sixteen simultaneous requests
on `boards-api.greenhouse.io` and none anywhere else. Each host gets its own slot and
its own courtesy delay, so the pool's width is spread across *different* employers'
infrastructure — which is both faster and politer than the serial version was.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from jobfinder.core.config import settings
from jobfinder.core.models import Company, Source
from jobfinder.sources.base import FetchResult, build_client, get_adapter

logger = logging.getLogger(__name__)


# Which host an adapter's requests land on. Adapters whose slug *is* the hostname
# (Recruitee, Personio, Workday) are resolved from the slug instead; everything else
# shares one API endpoint per platform, which is exactly what needs rate limiting.
SHARED_HOSTS = {
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "ashby": "api.ashbyhq.com",
    "workable": "apply.workable.com",
    "smartrecruiters": "api.smartrecruiters.com",
    "amazon": "amazon.jobs",
    "google": "google.com",
    "adzuna": "api.adzuna.com",
}


def host_for(adapter: str, slug: str) -> str:
    """The hostname a source's requests will hit, for rate-limiting purposes."""
    shared = SHARED_HOSTS.get(adapter)
    if shared:
        return shared

    if adapter == "recruitee":
        return f"{slug}.recruitee.com"
    if adapter == "personio":
        tenant, _, _ = slug.partition(":")
        return f"{tenant}.jobs.personio.de"
    if adapter == "workday":
        tenant, _, rest = slug.partition(":")
        wdhost, _, _ = rest.partition(":")
        return f"{tenant}.{wdhost}.myworkdayjobs.com"
    if adapter in {"jsonld", "sitemap"}:
        # The slug is a URL for extraction adapters.
        return urlsplit(slug if "//" in slug else f"//{slug}").netloc or slug

    return f"{adapter}:{slug}"


class HostLimiter:
    """One in-flight request and one courtesy delay per host.

    Holding the lock across the request serialises each host without serialising the
    crawl: a slow Workday tenant blocks only its own tenant, and the pool moves on to
    other employers meanwhile.
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay = delay_seconds
        self._locks: dict[str, threading.Lock] = {}
        self._next_allowed: dict[str, float] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def run(self, host: str, work):
        lock = self._lock_for(host)
        with lock:
            wait = self._next_allowed.get(host, 0.0) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return work()
            finally:
                self._next_allowed[host] = time.monotonic() + self._delay


@dataclass
class FetchJob:
    """One source, paired with the company it belongs to."""

    source: Source
    company: Company

    # Copied off the ORM objects at submit time. The worker thread must not touch a
    # Session-bound attribute: a lazy load from another thread is exactly the kind of
    # race that produces intermittent, unreproducible failures.
    adapter: str = ""
    slug: str = ""
    company_name: str = ""

    def __post_init__(self) -> None:
        self.adapter = self.source.adapter
        self.slug = self.source.slug
        self.company_name = self.company.name


def fetch_all(
    jobs: list[FetchJob],
    *,
    max_workers: int | None = None,
    client: httpx.Client | None = None,
) -> Iterator[tuple[FetchJob, FetchResult]]:
    """Fetch every source concurrently, yielding results as they land.

    Yields in completion order, not submission order, so a slow source cannot hold up
    reconciliation of the ones behind it.
    """
    if not jobs:
        return

    max_workers = max_workers or settings.crawl_max_workers
    limiter = HostLimiter(settings.request_delay_seconds)

    owns_client = client is None
    # httpx.Client is thread-safe and pools connections, so one shared client across the
    # pool is both correct and cheaper than one per worker.
    client = client or build_client()

    def run_one(job: FetchJob) -> FetchResult:
        adapter = get_adapter(job.adapter)
        if adapter is None:
            logger.error("no adapter registered for %r", job.adapter)
            return FetchResult.failed(f"unknown adapter {job.adapter!r}")

        host = host_for(job.adapter, job.slug)
        logger.info("fetching %s:%s (%s)", job.adapter, job.slug, job.company_name)
        return limiter.run(host, lambda: adapter.fetch(job.slug, client=client))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Future[FetchResult], FetchJob] = {
                pool.submit(run_one, job): job for job in jobs
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    yield job, future.result()
                except Exception as exc:  # noqa: BLE001 - a worker must never kill the crawl
                    logger.exception("fetch worker crashed for %s", job.slug)
                    yield job, FetchResult.failed(f"{type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            client.close()
