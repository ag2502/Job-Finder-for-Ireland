"""ORM models.

The schema is built around one non-negotiable property: a job posting is never deleted
and never wholesale-replaced. Every crawl reconciles against existing rows, and the
`source_crawls` audit trail records whether each source was actually reached — which is
what lets the reconciler tell "the job is gone" apart from "we could not look".
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CoverageState(str, enum.Enum):
    """How far a company has progressed toward having its jobs actually crawled.

    This is the accounting that turns "did we miss anyone?" into a dashboard: BLOCKED
    and UNRESOLVED are the work queue, and they are visible rather than silent.
    """

    UNRESOLVED = "unresolved"          # in the register, careers page not yet found
    NO_CAREERS_PAGE = "no_careers_page"  # checked, genuinely has none
    ATS_DETECTED = "ats_detected"      # pulling from a structured API
    BESPOKE_ADAPTER = "bespoke_adapter"  # custom adapter running
    GENERIC_EXTRACTION = "generic_extraction"  # JSON-LD / rendered extraction working
    BLOCKED = "blocked"                # careers page found but extraction failing


class JobStatus(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class CrawlStatus(str, enum.Enum):
    """Outcome of one source fetch.

    The distinction between OK and FAILED/PARTIAL is the most important thing in the
    schema. Absence logic runs only for OK; FAILED and PARTIAL leave jobs untouched.
    """

    OK = "ok"
    FAILED = "failed"
    PARTIAL = "partial"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(1024))
    careers_url: Mapped[str | None] = mapped_column(String(1024))

    # Registry provenance and priority.
    cro_number: Mapped[str | None] = mapped_column(String(64), index=True)
    is_public_listed: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_priority: Mapped[int] = mapped_column(Integer, default=5)
    coverage_state: Mapped[CoverageState] = mapped_column(
        Enum(CoverageState, native_enum=False), default=CoverageState.UNRESOLVED
    )
    seed_source: Mapped[str | None] = mapped_column(String(128))

    detection_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sources: Mapped[list["Source"]] = relationship(back_populates="company")

    __table_args__ = (UniqueConstraint("normalized_name", name="uq_company_name"),)

    def __repr__(self) -> str:
        return f"<Company {self.name!r} {self.coverage_state.value}>"


class Source(Base):
    """One crawlable endpoint. A company can have more than one."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)

    adapter: Mapped[str] = mapped_column(String(64), nullable=False)  # "greenhouse", ...
    slug: Mapped[str] = mapped_column(String(256), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Circuit breaker: after too many consecutive failures a source is disabled and
    # flagged rather than hammered.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("adapter", "slug", name="uq_source_adapter_slug"),
    )

    def __repr__(self) -> str:
        return f"<Source {self.adapter}:{self.slug}>"


class JobPosting(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity is (source_id, source_job_id): stable across runs and genuinely unique
    # within a source. `dedup_key` is deliberately NOT unique — it groups the same role
    # reported by different sources, but a company legitimately posts several openings
    # with an identical title and location, and a unique constraint here would silently
    # merge them into one. Grouping happens at read time instead.
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    source_job_id: Mapped[str] = mapped_column(String(256))
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2048))

    # location_raw is preserved verbatim, always. Normalization is additive.
    location_raw: Mapped[str | None] = mapped_column(String(1024))
    location_norm: Mapped[str | None] = mapped_column(String(512))
    is_dublin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_location_review: Mapped[bool] = mapped_column(Boolean, default=False)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Experience level, derived at reconciliation time so it can be filtered in SQL.
    # `min_years_required` is None for the ~55% of adverts that state no figure; that
    # means "unknown", never "zero", and must not be treated as disqualifying.
    min_years_required: Mapped[int | None] = mapped_column(Integer)
    years_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_internship: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_graduate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # The lifecycle fields that make the cumulative guarantee work.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.ACTIVE, index=True
    )
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("source_id", "source_job_id", name="uq_job_source_identity"),
        Index("ix_job_active_dublin", "status", "is_dublin"),
        Index("ix_job_source_status", "source_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<JobPosting {self.title!r} {self.status.value}>"


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sources_ok: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    sources_partial: Mapped[int] = mapped_column(Integer, default=0)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0)
    jobs_closed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_reopened: Mapped[int] = mapped_column(Integer, default=0)


class SourceCrawl(Base):
    """Per-source audit trail. The reconciler reads `status` from here to decide
    whether absence is meaningful."""

    __tablename__ = "source_crawls"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("crawl_runs.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    status: Mapped[CrawlStatus] = mapped_column(Enum(CrawlStatus, native_enum=False))
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    prev_jobs_found: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
