"""FastAPI + HTMX web portal.

One GDPR decision shapes this module: **the uploaded CV is never written to disk or to
the database.** It is parsed in memory, the derived signals (skills, seniority, field
guesses) go into a signed session cookie, and the document itself is discarded before
the response is sent.

That is not a shortcut, it is the stronger design. A CV is personal data — under Irish
and EU law, storing one makes you a controller with retention, access and deletion
duties, and it becomes the single most sensitive asset in the system. Extracting the few
hundred bytes of signal that matching actually needs and dropping the rest removes that
liability outright while losing nothing a searcher would notice.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from jobfinder.core.config import settings
from jobfinder.core.db import init_db, session_scope
from jobfinder.core.models import (
    Company,
    CoverageState,
    CrawlRun,
    CrawlStatus,
    JobPosting,
    JobStatus,
    Source,
    SourceCrawl,
)
from jobfinder.matching.rank import Candidate, rank_jobs
from jobfinder.matching import vocabulary
from jobfinder.matching.resume import parse_resume
from jobfinder.normalize.experience import matches_experience
from jobfinder.normalize.taxonomy import FIELDS

logger = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Result orderings offered in the form. Relevance is the default: it is the only one
# that uses the CV at all, and a bare date sort surfaces whatever was posted this
# morning regardless of whether the searcher could do the job.
SORTS = {
    "relevance": "Best match",
    "newest": "Newest first",
    "oldest": "Oldest first",
}
DEFAULT_SORT = "relevance"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Dublin Job Finder", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
templates = Jinja2Templates(directory=str(TEMPLATES))


def _profile(request: Request) -> dict | None:
    raw = request.session.get("profile")
    return json.loads(raw) if raw else None


def _base_context(request: Request) -> dict:
    return {
        "request": request,
        "fields": sorted(FIELDS.values(), key=lambda f: f.label),
        "profile": _profile(request),
        "sorts": SORTS,
    }


def _index_context(request: Request) -> dict:
    """Home page context. Shared with the upload error path, which renders the same
    template and would otherwise be missing the counts it interpolates."""
    with session_scope() as session:
        dublin = session.scalar(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.is_dublin.is_(True)
            )
        ) or 0
        companies = session.scalar(select(func.count()).select_from(Company)) or 0

    context = _base_context(request)
    context.update(dublin_count=dublin, company_count=companies)
    return context


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """The finder: upload form, with results rendered underneath once a search exists."""
    context = _index_context(request)
    profile = _profile(request)
    if profile:
        context.update(_search_results(profile, query=profile.get("query")))
    return templates.TemplateResponse(request, "finder.html", context)


@app.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    page: int = 1,
    resume: UploadFile | None = None,
    chosen_fields: list[str] = Form(default=[]),
    include_remote: str | None = Form(default=None),
    internships_only: str | None = Form(default=None),
    years: str | None = Form(default=None),
    q: str | None = Form(default=None),
    sort: str | None = Form(default=None),
):
    previous_profile = _profile(request) or {}

    # Roles are required. A CV on its own is too ambiguous to search on: it says what
    # someone has done, not what they want to do next, and a career-change or
    # broad-experience CV matches half the market. Paging re-submits the form with the
    # boxes still ticked, so the fallback to the existing session only covers that.
    effective_fields = chosen_fields or previous_profile.get("fields") or []
    if not effective_fields:
        context = _index_context(request)
        context["error"] = (
            "Pick at least one role or field you want to work in. Your CV tells us "
            "what you have done; the roles tell us what you are looking for."
        )
        if request.headers.get("HX-Request"):
            context.update(items=[], total=0, page=1, pages=1, query="", new_count=0)
        return templates.TemplateResponse(
            request, "finder.html", context, status_code=422
        )

    parsed = None
    cv_corpus_terms: list[str] = []
    if resume is not None and resume.filename:
        data = await resume.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            context = _index_context(request)
            context["error"] = "That file is larger than 5 MB. Please upload a smaller CV."
            return templates.TemplateResponse(
                request, "finder.html", context, status_code=413
            )
        parsed = parse_resume(data, resume.filename)
        del data  # the document itself goes no further
        # Read the CV against the vocabulary learned from the adverts themselves,
        # rather than a hand-written skills list. See matching/corpus.py.
        try:
            cv_corpus_terms = sorted(vocabulary.terms_for(parsed.text))[:400]
        except Exception:  # noqa: BLE001 - matching must still work without it
            logger.warning("corpus vocabulary unavailable", exc_info=True)
            cv_corpus_terms = []

    previous = previous_profile

    # Blank means "not stated", which shows every active opening. Only an explicit
    # number narrows the results, so an empty box must not collapse to zero.
    stated_years: int | None = None
    if years is not None and years.strip():
        try:
            stated_years = max(0, min(int(years.strip()), 50))
        except ValueError:
            stated_years = None

    # Paging re-submits the form, but a file input cannot be repopulated by the browser,
    # so the CV-derived signals are carried forward from the existing session rather
    # than silently reverting to a fields-only search on page two.
    profile = {
        "skills": sorted(parsed.skills) if parsed else previous.get("skills", []),
        "fields": effective_fields,
        "seniority": parsed.seniority if parsed else previous.get("seniority"),
        # Only what the searcher typed filters the results. A blank box means "not
        # stated" and returns every active opening, even when the CV implies a figure
        # - silently narrowing on a number the searcher never entered would hide roles
        # they never asked to hide. The CV's estimate is surfaced as a hint instead.
        "years": stated_years,
        "cv_years": parsed.years_experience if parsed else previous.get("cv_years"),
        "corpus_terms": cv_corpus_terms or previous.get("corpus_terms", []),
        "internships_only": bool(internships_only),
        # A trimmed excerpt is kept for lexical scoring; it is not the document.
        "text": (parsed.text[:6000] if parsed else previous.get("text", "")),
        "include_remote": bool(include_remote),
        "titles": parsed.titles[:5] if parsed else previous.get("titles", []),
        "query": q or None,
        # An unrecognised value falls back to relevance rather than erroring: the sort
        # is a presentation choice, not something worth failing a search over.
        "sort": sort if sort in SORTS else DEFAULT_SORT,
    }
    request.session["profile"] = json.dumps(profile)

    context = _base_context(request)
    context.update(_search_results(profile, query=q, page=page))

    # HTMX asks for the table alone; a normal form post gets the whole page back.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "_results.html", context)

    context.update(_index_context(request))
    return templates.TemplateResponse(request, "finder.html", context)


@app.get("/results", response_class=HTMLResponse)
def results(request: Request):
    """Superseded by the single-page finder; kept so old links still work."""
    return RedirectResponse("/", status_code=303)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything stored is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _experience_label(job: JobPosting) -> str | None:
    """Short human label for a posting's experience level.

    Says "about Ny" when the figure came from the title rather than the advert, so a
    guess is never presented as a stated requirement.
    """
    if job.is_internship:
        return "internship"
    if job.is_graduate:
        return "graduate / entry level"
    if job.min_years_required is None:
        return None
    if job.min_years_required == 0:
        return "no experience required"
    suffix = "y+" if not job.years_inferred else "y+ (estimated)"
    return f"{job.min_years_required}{suffix}"


def _prefer_direct_sources(session, rows: list[JobPosting]) -> list[JobPosting]:
    """Drop copies of a role that a more direct source also carries.

    `Source.tier` orders sources by how close each sits to the employer — an ATS API (1)
    over a generic extraction (3) over an aggregator (4) — so within a dedup group the
    lowest tier is the best copy of the job. Ties break on description length, a good
    proxy for which copy was truncated.

    **The unit of choice is the source, not the posting.** `dedup_key` is company plus
    canonical title plus location bucket, and that is deliberately not unique: Amazon
    really does have four separate "Software Development Engineer, AWS Database
    Migration Service" openings in Dublin, and MongoDB three "Technical Services
    Engineer". Keeping one posting per dedup group would have hidden 37 genuine Dublin
    openings in the live database. So a group keeps *every* posting from its winning
    source and discards only the copies that came from elsewhere — which is exactly the
    aggregator-duplicate case this exists to solve.
    """
    if not rows:
        return rows

    tiers = dict(session.execute(select(Source.id, Source.tier)).all())

    def rank(job: JobPosting) -> tuple[int, int]:
        return (tiers.get(job.source_id, 9), -len(job.description or ""))

    # dedup_key -> (best rank seen, the source that achieved it)
    winner: dict[str, tuple[tuple[int, int], int]] = {}
    for row in rows:
        scored = rank(row)
        current = winner.get(row.dedup_key)
        if current is None or scored < current[0]:
            winner[row.dedup_key] = (scored, row.source_id)

    return [row for row in rows if winner[row.dedup_key][1] == row.source_id]


def _search_results(profile: dict, *, query: str | None = None, page: int = 1) -> dict:
    """Rank the active jobs against a profile and build the template context."""
    candidate = Candidate(
        skills=set(profile.get("skills") or []),
        fields=profile.get("fields") or [],
        seniority=profile.get("seniority"),
        text=profile.get("text") or "",
        corpus_terms=set(profile.get("corpus_terms") or []),
    )

    with session_scope() as session:
        stmt = select(JobPosting).where(JobPosting.status == JobStatus.ACTIVE)
        if profile.get("include_remote"):
            stmt = stmt.where(
                (JobPosting.is_dublin.is_(True)) | (JobPosting.is_remote.is_(True))
            )
        else:
            stmt = stmt.where(JobPosting.is_dublin.is_(True))
        if query:
            stmt = stmt.where(JobPosting.title.ilike(f"%{query}%"))

        rows = session.execute(stmt).scalars().all()

        # The same role reaches the database more than once: an aggregator carries a
        # posting the employer's own board also carries. `dedup_key` groups them and the
        # direct source wins, so the searcher gets the full description and the real
        # apply URL rather than a redirect.
        rows = _prefer_direct_sources(session, rows)

        # Eligibility filtering happens here rather than in SQL so the rule lives in
        # one place; the candidate set for a single city is small enough that it costs
        # nothing.
        candidate_years = profile.get("years")
        want_internships = bool(profile.get("internships_only"))
        rows = [
            row
            for row in rows
            if matches_experience(
                job_min_years=row.min_years_required,
                job_is_internship=row.is_internship,
                job_is_graduate=row.is_graduate,
                candidate_years=candidate_years,
                want_internships=want_internships,
            )
        ]
        companies = {c.id: c.name for c in session.execute(select(Company)).scalars()}

        # Rank everything that matched rather than a fixed slice, so the reported
        # total is the real number of open roles and later pages are reachable.
        scored = rank_jobs(rows, candidate, limit=len(rows), only_relevant=True)
        by_id = {row.id: row for row in rows}

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        # "NEW" only means something once there is history to be new relative to. On a
        # freshly-built database every job was genuinely first seen today, so the badge
        # would mark all of them and tell the searcher nothing.
        oldest_run = session.scalar(select(func.min(CrawlRun.started_at)))
        if oldest_run is not None and oldest_run.tzinfo is None:
            oldest_run = oldest_run.replace(tzinfo=timezone.utc)
        has_history = bool(oldest_run and oldest_run < cutoff)

        items = []
        for entry in scored:
            job = by_id[entry.job_id]
            first_seen = job.first_seen_at
            if first_seen and first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
            items.append(
                {
                    "job": job,
                    "company": companies.get(job.company_id, "Unknown"),
                    "score": entry.score,
                    "why": entry.explain(),
                    "is_new": has_history and bool(first_seen and first_seen >= cutoff),
                    # Not every employer publishes a posting date - Google, for one,
                    # does not - so the date we first saw it is offered as a fallback
                    # and labelled as such rather than passed off as the posting date.
                    "posted": job.posted_at.strftime("%d %b %Y") if job.posted_at else None,
                    "first_seen": first_seen.strftime("%d %b") if first_seen else "",
                    "experience": _experience_label(job),
                }
            )

        # Relevance order comes for free from rank_jobs above. A date sort re-orders the
        # same eligible set instead: jobs with no usable date (neither a posted date nor
        # a first-seen fallback - vanishingly rare) sink to the end either way, since
        # there is nothing to confidently call oldest or newest about them.
        sort_mode = profile.get("sort") or DEFAULT_SORT
        if sort_mode in ("newest", "oldest"):
            dated = [i for i in items if i["job"].posted_at or i["job"].first_seen_at]
            undated = [i for i in items if not (i["job"].posted_at or i["job"].first_seen_at)]
            dated.sort(
                key=lambda i: _as_utc(i["job"].posted_at) or _as_utc(i["job"].first_seen_at),
                reverse=(sort_mode == "newest"),
            )
            items = dated + undated

        per_page = 25
        total = len(items)
        page = max(page, 1)
        start = (page - 1) * per_page
        page_items = items[start : start + per_page]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "query": query or "",
        "new_count": sum(1 for i in items if i["is_new"]),
        "skill_count": len(profile.get("skills") or []),
        "candidate_years": profile.get("years"),
        "internships_only": bool(profile.get("internships_only")),
    }


@app.post("/reset")
def reset(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    with session_scope() as session:
        totals = {
            "companies": session.scalar(select(func.count()).select_from(Company)) or 0,
            "sources": session.scalar(select(func.count()).select_from(Source)) or 0,
            "active": session.scalar(
                select(func.count()).select_from(JobPosting).where(
                    JobPosting.status == JobStatus.ACTIVE
                )
            ) or 0,
            "dublin": session.scalar(
                select(func.count()).select_from(JobPosting).where(
                    JobPosting.status == JobStatus.ACTIVE,
                    JobPosting.is_dublin.is_(True),
                )
            ) or 0,
            "closed": session.scalar(
                select(func.count()).select_from(JobPosting).where(
                    JobPosting.status == JobStatus.CLOSED
                )
            ) or 0,
            "review": session.scalar(
                select(func.count()).select_from(JobPosting).where(
                    JobPosting.needs_location_review.is_(True)
                )
            ) or 0,
        }

        coverage = session.execute(
            select(Company.coverage_state, func.count())
            .group_by(Company.coverage_state)
        ).all()

        listed = session.execute(
            select(Company).where(Company.is_public_listed.is_(True))
        ).scalars().all()

        runs = session.execute(
            select(CrawlRun).order_by(CrawlRun.id.desc()).limit(8)
        ).scalars().all()

        problems = session.execute(
            select(SourceCrawl, Source, Company)
            .join(Source, SourceCrawl.source_id == Source.id)
            .join(Company, Source.company_id == Company.id)
            .where(SourceCrawl.status != CrawlStatus.OK)
            .order_by(SourceCrawl.id.desc())
            .limit(20)
        ).all()

        per_source = session.execute(
            select(Company.name, Source.adapter, Source.slug, func.count(JobPosting.id))
            .join(Source, Source.company_id == Company.id)
            .outerjoin(
                JobPosting,
                (JobPosting.source_id == Source.id)
                & (JobPosting.status == JobStatus.ACTIVE)
                & (JobPosting.is_dublin.is_(True)),
            )
            .group_by(Company.name, Source.adapter, Source.slug)
            .order_by(func.count(JobPosting.id).desc())
        ).all()

    context = _base_context(request)
    context.update(
        totals=totals,
        coverage=[(state.value, count) for state, count in coverage],
        listed=listed,
        runs=runs,
        problems=problems,
        per_source=per_source,
        states=[s.value for s in CoverageState],
    )
    return templates.TemplateResponse(request, "admin.html", context)


@app.get("/directory", response_class=HTMLResponse)
def directory(request: Request, q: str = "", show: str = "all"):
    """Every employer in the registry, crawlable or not.

    This is the honest answer to "is my company covered?". No crawler will ever reach
    100% of Irish employers — some render their listings entirely in JavaScript, some
    sit behind a bot wall, some have no careers page at all. What *is* achievable is
    that no employer is silently absent: every company in the registry appears here
    with either its live Dublin openings or a direct link to its careers page, and its
    coverage state says which and why.

    A company with a careers link and no crawled jobs is one click from the searcher,
    which is the whole difference between "missing" and "not automated".
    """
    query = (q or "").strip()

    with session_scope() as session:
        live_counts = dict(
            session.execute(
                select(JobPosting.company_id, func.count())
                .where(
                    JobPosting.status == JobStatus.ACTIVE,
                    JobPosting.is_dublin.is_(True),
                )
                .group_by(JobPosting.company_id)
            ).all()
        )

        stmt = select(Company)
        if query:
            stmt = stmt.where(Company.name.ilike(f"%{query}%"))
        companies = session.execute(stmt).scalars().all()

        crawled_states = {
            CoverageState.ATS_DETECTED,
            CoverageState.BESPOKE_ADAPTER,
            CoverageState.GENERIC_EXTRACTION,
        }

        rows = []
        for company in companies:
            count = live_counts.get(company.id, 0)
            rows.append(
                {
                    "name": company.name,
                    "jobs": count,
                    "careers_url": company.careers_url or company.website,
                    "state": company.coverage_state.value,
                    "crawled": company.coverage_state in crawled_states,
                    "priority": company.coverage_priority,
                }
            )

        if show == "hiring":
            rows = [r for r in rows if r["jobs"]]
        elif show == "linked":
            rows = [r for r in rows if not r["jobs"] and r["careers_url"]]

        # Employers with live roles lead, then those with the most complete coverage,
        # then alphabetically. A searcher scanning this wants the actionable rows first.
        rows.sort(key=lambda r: (-r["jobs"], not r["crawled"], r["name"].lower()))

        total = len(companies)
        with_jobs = sum(1 for r in rows if r["jobs"])
        linked = sum(1 for r in rows if not r["jobs"] and r["careers_url"])

    context = _base_context(request)
    context.update(
        {
            "rows": rows[:400],
            "truncated": max(0, len(rows) - 400),
            "total": total,
            "with_jobs": with_jobs,
            "linked": linked,
            "query": query,
            "show": show,
        }
    )
    return templates.TemplateResponse(request, "directory.html", context)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", _base_context(request))
