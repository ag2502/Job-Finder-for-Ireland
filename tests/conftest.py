from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobfinder.core.models import Base, Company, CoverageState, CrawlRun, Source
from jobfinder.sources.base import FetchResult, RawJob
from jobfinder.core.models import CrawlStatus


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with maker() as s:
        yield s


@pytest.fixture
def company(session: Session) -> Company:
    c = Company(
        name="Testcorp",
        normalized_name="testcorp",
        coverage_state=CoverageState.ATS_DETECTED,
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def source(session: Session, company: Company) -> Source:
    s = Source(company_id=company.id, adapter="greenhouse", slug="testcorp", tier=1)
    session.add(s)
    session.flush()
    return s


@pytest.fixture
def run(session: Session) -> CrawlRun:
    r = CrawlRun()
    session.add(r)
    session.flush()
    return r


def make_job(job_id: str, title: str = "Software Engineer", location: str = "Dublin, Ireland") -> RawJob:
    return RawJob(
        source_job_id=job_id,
        title=title,
        url=f"https://example.com/jobs/{job_id}",
        location_raw=location,
    )


def ok_result(*job_ids: str, **kwargs) -> FetchResult:
    return FetchResult(
        status=CrawlStatus.OK,
        jobs=[make_job(j, **kwargs) for j in job_ids],
    )
