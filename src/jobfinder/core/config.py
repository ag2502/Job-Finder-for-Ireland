"""Application settings, read from the environment or a local .env file."""

from __future__ import annotations

from pathlib import Path

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

    log_level: str = "INFO"

    # Signs the session cookie holding a searcher's derived profile. Override in
    # production via JOBFINDER_SESSION_SECRET; the default is for local use only.
    session_secret: str = "dev-only-change-me"


settings = Settings()
