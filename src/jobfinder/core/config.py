"""Application settings, read from the environment or a local .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="JOBFINDER_", extra="ignore"
    )

    # SQLite by default so the pipeline runs with no external services. Production
    # points this at Neon Postgres; nothing else in the codebase needs to change.
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'jobfinder.db'}"

    @field_validator("database_url")
    @classmethod
    def _pin_postgres_driver(cls, url: str) -> str:
        """Name the Postgres driver explicitly in the URL.

        Hosted providers hand out a bare `postgresql://...` string, and SQLAlchemy maps
        that to psycopg2 — a different, unlisted package. The pasted URL then fails at
        connect time with `ModuleNotFoundError: psycopg2`, which reads like a broken
        install rather than a URL that needs a suffix. Rewriting the scheme here means
        the string copied out of the Neon dashboard works unedited.

        `postgres://` is accepted too: several providers still emit that older form,
        which SQLAlchemy rejects outright.
        """
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    # A crawl that returns less than this fraction of the previous run's job count is
    # treated as PARTIAL rather than OK, so a silent upstream degradation cannot close
    # jobs en masse. See pipeline/state.py.
    volume_drop_threshold: float = 0.5

    # The breaker only engages once a board is big enough for a collapse to be
    # implausible. A company with three openings filling all three is ordinary
    # business; a board going from 500 postings to 2 is a broken adapter. Without this
    # floor, small boards could never legitimately empty and their jobs would stay
    # active forever.
    volume_drop_min_baseline: int = 10

    # How many consecutive successful crawls must miss a job before it is closed.
    # One grace run absorbs transient flakiness at the cost of a day of staleness.
    misses_before_close: int = 2

    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3

    # Identifies the crawler to the sites it visits, with a contact route.
    user_agent: str = (
        "JobFinderBot/0.1 (+https://github.com/jobfinder; Dublin job aggregator)"
    )

    # Per-domain politeness delay in seconds.
    request_delay_seconds: float = 1.0

    # Width of the fetch pool. Because the limiter allows one in-flight request per
    # host, this is a cap on how many *different* employers are contacted at once
    # rather than on load against any one of them.
    crawl_max_workers: int = 16

    # Detection is a burst of one-off requests to thousands of *different* hosts, so it
    # can run wider than the crawl without any one site seeing concentrated traffic.
    detect_max_workers: int = 24

    # A whole-company wall-clock budget for detection, and a per-request timeout well
    # under the crawler's. Detection makes up to ~20 requests per company across the
    # open internet, so without a ceiling one unresponsive site holds a worker forever
    # and a sweep of thousands never finishes. Exceeding the budget is not an error: it
    # returns whatever was found so far, which is usually the careers URL.
    detect_budget_seconds: float = 45.0
    detect_timeout_seconds: float = 12.0

    # Cap on a single response body read during detection. Detection only ever needs
    # the markup near the top of a page; without a cap, one site streaming a huge file
    # stalls a worker and consumes memory.
    detect_max_bytes: int = 2_000_000

    # Whole-sweep ceiling. Individual probes are already budgeted, but a worker wedged
    # in a call no timeout reaches (name resolution, most often) cannot be cancelled —
    # only abandoned. The sweep therefore stops waiting at this point and returns what
    # it has; unfinished companies keep their state and are retried next run.
    detect_sweep_deadline_seconds: float = 1800.0

    # Sources whose company sits below this priority are crawled only when their last
    # success is older than `stale_after_hours`. A registry of thousands is mostly long
    # tail: re-fetching a 12-person consultancy every six hours costs far more than the
    # freshness it buys, while the multinationals that carry most of the roles stay on
    # every run.
    priority_always_crawl: int = 2
    stale_after_hours: int = 24

    # Tier 4 aggregator credentials. Unset by default: every aggregator adapter reports
    # FAILED when its key is missing, which the reconciler treats exactly like an
    # unreachable source — recorded, and closing nothing.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""
    careerjet_affiliate_id: str = ""

    log_level: str = "INFO"

    # Signs the session cookie holding a searcher's derived profile. Override in
    # production via JOBFINDER_SESSION_SECRET; the default is for local use only.
    session_secret: str = "dev-only-change-me"


settings = Settings()
