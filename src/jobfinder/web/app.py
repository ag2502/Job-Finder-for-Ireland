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

import hmac
import json
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, select
from itsdangerous import BadSignature, URLSafeTimedSerializer
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

import httpx

from jobfinder.core import supabase
from jobfinder.core.config import settings
from jobfinder.core.db import engine, init_db, session_scope
from jobfinder.core.models import (
    Company,
    CrawlRun,
    JobPosting,
    JobStatus,
    Source,
)
from jobfinder.matching import rank
from jobfinder.matching.rank import Candidate, rank_jobs
from jobfinder.matching import vocabulary
from jobfinder.matching import llm_profile
from jobfinder.matching.resume import parse_resume
from jobfinder.normalize.dedup import (
    canonical_title,
    key_for,
    location_bucket,
    normalize_company_name,
    same_employer,
)
from jobfinder.normalize.experience import matches_experience
from jobfinder.normalize.taxonomy import ALL_SKILLS, FIELDS, GROUPS

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


# Vercel sets VERCEL on every deployment. Locally the finder is a personal tool; there it
# is a public site, so the defaults that are harmless on a laptop become holes.
PUBLIC_DEPLOYMENT = bool(os.environ.get("VERCEL"))

if PUBLIC_DEPLOYMENT and settings.session_secret == "dev-only-change-me":
    # The session cookie carries a visitor's search profile, including terms read from
    # their CV. Signed with a secret published in this repository, anyone could forge one.
    # Refusing to start is louder than any warning.
    raise RuntimeError(
        "JOBFINDER_SESSION_SECRET is not set. Add a long random value in the Vercel "
        "project's environment variables before deploying."
    )

app = FastAPI(title="Dublin Job Finder", lifespan=lifespan)

# Everything a signed-out visitor may still reach. Privacy is on the list deliberately:
# someone has to be able to read what an account will store about them *before* being
# asked to create one, and a policy you can only see once you have signed up is no use
# to the person deciding whether to.
# Pages that ARE the account, and therefore cannot be shown without one. Everything
# else - the finder, the results, the employer register - is open to anyone.
#
# It was the other way round until launch: every path except a handful was gated, so a
# visitor arriving from a link saw a sign-in wall instead of a single job. For a product
# whose whole pitch is "every opening in one place", asking for a password before
# showing any of them is the wrong first impression. Applying still needs an account,
# because applying is what the account exists to record.
ACCOUNT_PATHS = frozenset({"/profile", "/applications", "/saved"})


@app.middleware("http")
async def _require_account(request: Request, call_next):
    """Send signed-out visitors to the sign-in page, for the account's own pages only.

    The gate is only armed when there is actually an accounts service to sign in to.
    Without one `/login` does not exist, so gating would redirect every visitor to a 404
    and take the whole site down - the environment variables going missing should cost
    the sign-in button, not the job search.
    """
    if supabase.configured() and request.url.path in ACCOUNT_PATHS:
        if _account(request) is None:
            # A redirect is right for someone typing an address, and wrong for anything
            # else: htmx would follow it and swap a whole sign-in page into whatever
            # element made the request - a table cell, or an Apply button.
            if request.method == "GET" and not request.headers.get("HX-Request"):
                nxt = quote(request.url.path, safe="/")
                return RedirectResponse(f"/login?next={nxt}", status_code=303)
            return PlainTextResponse("Sign in to continue.", status_code=401)
    return await call_next(request)


# The account lives in a cookie of its own, not in the session beside the search profile.
# Browsers silently drop any cookie over about 4KB, and the two together did not fit: the
# profile runs to ~2.8KB signed once a CV is read, and a Supabase access token is a JWT
# of ~1KB before it is encoded twice more - larger again for a Google account, whose
# token carries the name and picture. Signed in with a CV, the whole session was being
# thrown away by the browser, which looked like being signed out at random.
ACCOUNT_COOKIE = "sp_account"
ACCOUNT_MAX_AGE = 60 * 60 * 24 * 30
_account_signer = URLSafeTimedSerializer(settings.session_secret, salt="account")


@app.middleware("http")
async def _account_cookie(request: Request, call_next):
    """Read the account cookie on the way in; write back whatever the request changed.

    Added after `_require_account`, so it runs first and the gate can see the account.
    """
    raw = request.cookies.get(ACCOUNT_COOKIE)
    data = None
    if raw:
        try:
            data = _account_signer.loads(raw, max_age=ACCOUNT_MAX_AGE)
        except BadSignature:
            data = None
    request.state.account_data = data

    response = await call_next(request)

    if hasattr(request.state, "account_write"):
        value = request.state.account_write
        if value is None:
            response.delete_cookie(ACCOUNT_COOKIE, path="/")
        else:
            response.set_cookie(
                ACCOUNT_COOKIE,
                _account_signer.dumps(value),
                max_age=ACCOUNT_MAX_AGE,
                path="/",
                httponly=True,
                samesite="lax",
                secure=PUBLIC_DEPLOYMENT,
            )
    return response


# Added last, so it wraps the middleware above: Starlette runs the most recently added
# first, and `_require_account` reads `request.session`, which does not exist until this
# one has run.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
templates = Jinja2Templates(directory=str(TEMPLATES))


# An em dash, or an en dash standing in for one with a space either side, and the space
# around it. Written as escapes so the rule itself is readable in any editor.
_DASH_RUN = re.compile("\\s*\u2014\\s*|\\s+\u2013\\s+")


def _no_em_dashes(value):
    """Every value a template prints, with em dashes turned into commas.

    The site's own copy has none, by house style, and does not use hyphens in their
    place either. Job titles and company names arrive however each employer typed them,
    em dashes included, and this is the one place that catches all of them: "Engineer,
    Payments, Dublin" reads as cleanly as the original. An en dash inside a number
    range is ordinary punctuation and stays.
    """
    if isinstance(value, str) and ("\u2014" in value or "\u2013" in value):
        cleaned = _DASH_RUN.sub(", ", value).removeprefix(", ")
        # Macro output is already-safe HTML; handing it back as a plain string would
        # have it escaped a second time.
        return Markup(cleaned) if isinstance(value, Markup) else cleaned
    return value


templates.env.finalize = _no_em_dashes


def _profile(request: Request) -> dict | None:
    raw = request.session.get("profile")
    return json.loads(raw) if raw else None


def _account(request: Request) -> supabase.Account | None:
    """The signed-in searcher, refreshing the access token when it has aged out.

    Supabase access tokens last an hour. Rather than make someone sign in again every
    hour, the refresh token stored beside it buys a new one; only if that also fails is
    the session cleared, which is the real "you are signed out" case.

    Resolved at most once per request and remembered on `request.state`. Both the sign-in
    gate and the page context need the account, and refreshing twice would spend the
    refresh token twice - Supabase rotates them, so the second attempt presents one that
    has already been used and the searcher is signed out mid-request for no reason.
    """
    if hasattr(request.state, "account"):
        return request.state.account

    data = getattr(request.state, "account_data", None)
    if data is None and "account" in request.session:
        # Signed in before the account had a cookie of its own: move it across, which
        # also shrinks the session back under the size browsers keep.
        data = request.session.pop("account")
        request.state.account_write = data

    account = supabase.Account.from_session(data)
    if account is not None and account.expired:
        try:
            account = supabase.refresh(account)
        except (supabase.SupabaseError, httpx.HTTPError):
            _forget_account(request)
            return None
        _remember_account(request, account)
        return account

    request.state.account = account
    return account


def _remember_account(request: Request, account: supabase.Account) -> None:
    request.state.account = account
    request.state.account_write = account.to_session()


def _start_fresh(request: Request) -> None:
    """Drop the search in progress when the person at the keyboard changes.

    The search lives in its own cookie, apart from the account, so it outlived signing
    in and out: the next person to sign in on the same browser found the last one's
    fields ticked, and the skills read from their CV still shaping the ranking. Called
    on every sign-in and sign-out, never on the silent hourly token refresh.
    """
    request.session.pop("profile", None)


def _forget_account(request: Request) -> None:
    request.state.account = None
    request.state.account_write = None
    request.session.pop("account", None)


def _applied_keys(account: supabase.Account | None) -> set[str]:
    """The adverts this account has applied to.

    An outage here must not take the search down with it: the worst case of failing open
    is that a job the searcher has already applied to appears in the list, which is the
    behaviour they had before accounts existed.
    """
    if account is None:
        return set()
    try:
        return {row["advert_key"] for row in supabase.list_applications(account)}
    except (supabase.SupabaseError, httpx.HTTPError):
        logger.warning("could not load applications; showing every job", exc_info=True)
        return set()


def _account_marks(account: supabase.Account | None) -> tuple[set[str], set[str]]:
    """Applied and saved keys together, fetched side by side rather than one after the
    other: each is a round trip to Supabase, and a search waits on both."""
    if account is None:
        return set(), set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        applied = pool.submit(_applied_keys, account)
        saved = pool.submit(_saved_keys, account)
        return applied.result(), saved.result()


def _saved_keys(account: supabase.Account | None) -> set[str]:
    """The adverts this account has saved for later.

    Fails open exactly as `_applied_keys` does. The worst case is a Save button that
    shows unsaved on something already saved; pressing it again upserts, so nothing is
    lost and the search stays up.
    """
    if account is None:
        return set()
    try:
        return {row["advert_key"] for row in supabase.list_saved(account)}
    except (supabase.SupabaseError, httpx.HTTPError):
        logger.warning("could not load saved jobs", exc_info=True)
        return set()


def _base_context(request: Request) -> dict:
    account = _account(request)
    return {
        "request": request,
        # Grouped rather than one flat alphabetical run. At 24 fields a single row of
        # tabs was scannable; at 56 it is a wall, and the searcher who wants Machine
        # Learning should not have to read past Hospitality to find it.
        "field_groups": [
            (label, [f for f in FIELDS.values() if f.group == group])
            for group, label in GROUPS
        ],
        "profile": _profile(request),
        "sorts": SORTS,
        "account": account,
        "accounts_enabled": supabase.configured(),
    }


# The session lives in one signed cookie, and browsers drop a cookie over about 4KB
# without a word - no error, no warning, the request simply arrives without it. The
# profile was 8,571 bytes whenever a CV was attached, so every CV search was running
# with a session the browser had already thrown away.
#
# The document itself is gone from it. A 6,000-character excerpt was 95% of that weight,
# and it bought nothing: `BM25Index.score` scores `set(query_tokens)`, so only the
# distinct terms count, and `skills` plus `corpus_terms` already carry them. Measured
# against the live Dublin set, dropping it left the top 25 results identical. It also
# ends a real leak, since the excerpt carried whatever the CV said - including the
# email address the privacy page promises is never kept.
#
# These caps keep the rest inside the budget: roughly 2KB of JSON, ~2.8KB signed.
MAX_SESSION_SKILLS = 60
MAX_SESSION_TERMS = 120


# Where each company's logo comes from. Most registry rows carry a website, and the
# favicon service turns that into a proper app icon; these are the few whose website is
# a jobs subdomain or a brand the service has no icon for.
LOGO_DOMAINS = {
    "Amazon": "amazon.com",
    "BNY Mellon": "bnymellon.com",
}

# The platforms jobs are read from, in words a job seeker would recognise, with the
# domain their logo is fetched from. Anything unlisted falls back to its adapter name.
PLATFORMS = {
    "workday": ("Workday", "workday.com"),
    "greenhouse": ("Greenhouse", "greenhouse.com"),
    "gradireland": ("gradireland", "gradireland.com"),
    "amazon": ("amazon.jobs", "amazon.com"),
    "publicjobs": ("publicjobs.ie", "publicjobs.ie"),
    "google": ("Google Careers", "google.com"),
    "oracle_recruiting": ("Oracle Recruiting", "oracle.com"),
    "jsonld": ("Company careers sites", ""),
    "careers_html": ("Company careers sites", ""),
    "successfactors": ("SAP SuccessFactors", "sap.com"),
    "oleeo": ("Oleeo", "oleeo.com"),
    "workable": ("Workable", "workable.com"),
    "ashby": ("Ashby", "ashbyhq.com"),
    "smartrecruiters": ("SmartRecruiters", "smartrecruiters.com"),
    "eightfold": ("Eightfold", "eightfold.ai"),
    "icims": ("iCIMS", "icims.com"),
    "pinpoint": ("Pinpoint", "pinpointhq.com"),
    "candidatemanager": ("CandidateManager", "candidatemanager.net"),
    "bamboohr": ("BambooHR", "bamboohr.com"),
    "hirehive": ("HireHive", "hirehive.com"),
    "lever": ("Lever", "lever.co"),
    "teamtailor": ("Teamtailor", "teamtailor.com"),
    "personio": ("Personio", "personio.com"),
    "recruitee": ("Recruitee", "recruitee.com"),
    "breezy": ("Breezy HR", "breezy.hr"),
    "occupop": ("Occupop", "occupop.com"),
    "adzuna": ("Adzuna", "adzuna.ie"),
}

# Names used in the companies page's description. Only the ones actually hiring today
# are printed, so the sentence can never name a company that has nothing open.
GLOBAL_NAMES = (
    "Google", "Amazon", "Microsoft", "Meta", "Stripe", "Salesforce", "Mastercard",
    "OpenAI", "Citi", "Accenture", "MongoDB", "Anthropic", "SAP",
)
IRISH_NAMES = (
    "ESB", "Dunnes Stores", "Davy", "Version 1", "EirGrid", "Intercom", "CarTrawler",
    "Arthur Cox", "Mason Hayes & Curran", "Penneys", "Codec",
)


def _logo_domain(name: str, website: str | None) -> str:
    """The bare domain a company's logo is fetched for, or "" when there is none."""
    if name in LOGO_DOMAINS:
        return LOGO_DOMAINS[name]
    if not website:
        return ""
    host = urlparse(website if "//" in website else f"//{website}").hostname or ""
    return host.removeprefix("www.")


def _hiring_employers(session) -> list[dict]:
    """Every company with an open Dublin job, biggest first, with its logo domain and
    the platforms its jobs were read from."""
    rows = session.execute(
        select(
            Company.id,
            Company.name,
            Company.website,
            Source.adapter,
            func.count(JobPosting.id),
        )
        .join(JobPosting, JobPosting.company_id == Company.id)
        .join(Source, Source.id == JobPosting.source_id)
        .where(_is_offerable(), JobPosting.is_dublin.is_(True))
        .group_by(Company.id, Company.name, Company.website, Source.adapter)
    ).all()

    employers: dict[int, dict] = {}
    for company_id, name, website, adapter, n in rows:
        entry = employers.setdefault(
            company_id,
            {
                "id": company_id,
                "name": name,
                "domain": _logo_domain(name, website),
                "jobs": 0,
                "platforms": [],
                "adapters": set(),
            },
        )
        entry["jobs"] += n
        entry["adapters"].add(adapter)
        label = PLATFORMS.get(adapter, (adapter, ""))[0]
        if label not in entry["platforms"]:
            entry["platforms"].append(label)
    return sorted(employers.values(), key=lambda e: (-e["jobs"], e["name"].lower()))


def _index_context(request: Request) -> dict:
    """Home page context. Shared with the upload error path, which renders the same
    template and would otherwise be missing the counts it interpolates."""
    with session_scope() as session:
        # The same rule the search uses, so the headline count cannot promise more
        # openings than a search will actually offer.
        dublin = session.scalar(
            select(func.count()).select_from(JobPosting).where(
                _is_offerable(), JobPosting.is_dublin.is_(True)
            )
        ) or 0
        companies = session.scalar(select(func.count()).select_from(Company)) or 0

        # "All of Dublin in one place" is a claim, and naming the companies is how a
        # visitor checks it rather than taking it. The home page shows the biggest few
        # as open windows, each carrying real titles from that company, and links the
        # rest to /companies.
        hiring_employers = _hiring_employers(session)
        showcase = [e for e in hiring_employers if e["domain"]][:8]
        for employer in showcase:
            employer["titles"] = session.scalars(
                select(JobPosting.title)
                .where(
                    _is_offerable(),
                    JobPosting.is_dublin.is_(True),
                    JobPosting.company_id == employer["id"],
                )
                .order_by(JobPosting.first_seen_at.desc())
                .limit(3)
            ).all()

    context = _base_context(request)
    context.update(
        dublin_count=dublin,
        company_count=companies,
        # The registry size and the number of employers actually hiring are different
        # numbers, and only the second one is true of the jobs on the page.
        employers_hiring=len(hiring_employers),
        hiring_employers=hiring_employers,
        showcase=showcase,
    )
    return context


STATIC = Path(__file__).parent / "static"


@app.get("/static/{name}", include_in_schema=False)
def static_file(name: str):
    """The site's own scripts, cached for a year.

    Every file here carries its version in its name, so a new version is a new URL and
    nothing stale can be served. htmx used to come from unpkg, render-blocking in the
    <head>, behind a redirect: a third-party round trip before every first paint.
    """
    path = (STATIC / name).resolve()
    if path.parent != STATIC.resolve() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        path, headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """The finder: upload form, with results rendered underneath once a search exists."""
    # Supabase falls back to the project's Site URL - this page - when the callback
    # address is not on its allow list. Finish the sign-in rather than dropping it.
    if request.query_params.get("code") and "oauth" in request.session:
        return RedirectResponse(f"/auth/callback?{request.url.query}", status_code=303)

    context = _index_context(request)
    profile = _profile(request)
    if profile:
        applied, saved_keys = _account_marks(_account(request))
        context.update(
            _search_results(
                profile,
                query=profile.get("query"),
                applied_keys=applied,
                saved_keys=saved_keys,
                show_applied=bool(profile.get("show_applied")),
            )
        )
    return templates.TemplateResponse(request, "finder.html", context)


@app.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    page: int = 1,
    resume: UploadFile | None = None,
    chosen_fields: list[str] = Form(default=[]),
    include_remote: str | None = Form(default=None),
    internships_only: str | None = Form(default=None),
    graduate_only: str | None = Form(default=None),
    years: str | None = Form(default=None),
    q: str | None = Form(default=None),
    sort: str | None = Form(default=None),
    show_applied: str | None = Form(default=None),
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
    reading: llm_profile.CvReading | None = None
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
        # The rules above read the CV as a bag of keywords; this reads it as a
        # document. It is what lets a CV that never says "machine learning" still be
        # recognised as one, and it returns None whenever it cannot run, leaving the
        # rule-based reading in place. See matching/llm_profile.py.
        reading = llm_profile.read_cv(parsed.text)
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
        # "15+" is the top of the list; the form sends 16 for it, but the label is
        # accepted too so a hand-typed or bookmarked value means the same thing.
        raw_years = years.strip().removesuffix("+")
        bump = 1 if years.strip().endswith("+") else 0
        try:
            stated_years = max(0, min(int(raw_years) + bump, 50))
        except ValueError:
            stated_years = None

    # Paging re-submits the form, but a file input cannot be repopulated by the browser,
    # so the CV-derived signals are carried forward from the existing session rather
    # than silently reverting to a fields-only search on page two.
    # Only the tokens the taxonomy knows can score against a job, because the other
    # half of that comparison is `extract_skills` over the advert. A skill the model
    # named that nothing in the corpus asks for would be a word in a list, not a match.
    cv_skills: list[str] = []
    if parsed:
        cv_skills = sorted(
            parsed.skills | ({s for s in reading.skills if s in ALL_SKILLS}
                             if reading else set())
        )

    profile = {
        "skills": (cv_skills if parsed else previous.get("skills", []))[
            :MAX_SESSION_SKILLS
        ],
        "fields": effective_fields,
        # What the CV says it is, as opposed to what the searcher ticked. Kept apart
        # from "fields" on purpose: a CV is a record of what someone has done, and the
        # boxes are a statement of what they want to do next. The reading is offered
        # back to them in the results header, never substituted for their choice.
        "cv_fields": (
            reading.fields if reading
            else ([] if parsed else previous.get("cv_fields", []))
        ),
        "cv_summary": (
            reading.summary if reading
            else ("" if parsed else previous.get("cv_summary", ""))
        ),
        "seniority": (
            (reading.seniority if reading and reading.seniority else parsed.seniority)
            if parsed else previous.get("seniority")
        ),
        # Only what the searcher typed filters the results. A blank box means "not
        # stated" and returns every active opening, even when the CV implies a figure
        # - silently narrowing on a number the searcher never entered would hide roles
        # they never asked to hide. The CV's estimate is surfaced as a hint instead.
        "years": stated_years,
        # The model's figure wins where it has one: `detect_years` falls back to the
        # span between the earliest and latest years printed anywhere on the page, so a
        # CV listing a 2016 school-leaving date reads as nine years of experience.
        "cv_years": (
            (reading.years_experience
             if reading and reading.years_experience is not None
             else parsed.years_experience)
            if parsed else previous.get("cv_years")
        ),
        "corpus_terms": (cv_corpus_terms or previous.get("corpus_terms", []))[
            :MAX_SESSION_TERMS
        ],
        "internships_only": bool(internships_only),
        "graduate_only": bool(graduate_only),
        "include_remote": bool(include_remote),
        "titles": parsed.titles[:5] if parsed else previous.get("titles", []),
        "query": q or None,
        # An unrecognised value falls back to relevance rather than erroring: the sort
        # is a presentation choice, not something worth failing a search over.
        "sort": sort if sort in SORTS else DEFAULT_SORT,
        # Remembered so paging and re-sorting keep the choice, like every other control.
        "show_applied": bool(show_applied),
    }
    request.session["profile"] = json.dumps(profile)

    applied, saved_keys = _account_marks(_account(request))
    context = _base_context(request)
    context.update(
        _search_results(
            profile,
            query=q,
            page=page,
            applied_keys=applied,
            saved_keys=saved_keys,
            show_applied=bool(show_applied),
        )
    )

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


def _is_offerable():
    """Only openings the last successful crawl of their source actually returned.

    A job stays ACTIVE for one grace crawl after it stops appearing, so that a single odd
    response cannot close a live role - but a role that has already gone missing once is,
    far more often than not, gone. Sources are re-crawled about daily, so offering that
    grace period meant showing adverts up to two days after the employer took them down,
    and the Apply button led to "Job not found".

    `consecutive_misses` resets to zero the moment a job is seen again, so nothing is
    hidden permanently. This only declines to vouch for a listing the employer's own
    board has stopped returning.
    """
    return and_(
        JobPosting.status == JobStatus.ACTIVE,
        JobPosting.consecutive_misses == 0,
    )


def _prefer_direct_sources(
    session, rows: list[JobPosting], keys: dict[int, tuple[str, str, str]] | None = None
) -> list[JobPosting]:
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

    # The caller may already have these. Search does, because it needs the *folded*
    # company name - the one every copy of an advert agrees on - to build the key it
    # stores against an application. Recomputing after the losing copies are dropped
    # would hash whichever spelling happened to survive instead.
    if keys is None:
        keys = _grouping_keys(session, rows)

    # group key -> (best rank seen, the source that achieved it)
    winner: dict[tuple[str, str, str], tuple[tuple[int, int], int]] = {}
    for row in rows:
        scored = rank(row)
        key = keys[row.id]
        current = winner.get(key)
        if current is None or scored < current[0]:
            winner[key] = (scored, row.source_id)

    return [row for row in rows if winner[keys[row.id]][1] == row.source_id]


def _grouping_keys(
    session, rows: list[JobPosting]
) -> dict[int, tuple[str, str, str]]:
    """Group each posting with the other copies of the same advert.

    The stored `dedup_key` is not used. It was computed when the row was crawled, so it
    freezes in whatever the company-name rules were that day, and every later improvement
    to them reaches only newly-crawled rows. Recomputing here costs nothing on a city's
    worth of jobs and applies the current rules to everything.

    Company names then get one further pass. An aggregator carries the employer's name
    however it was typed into it, so ByrneWallace arrived three ways - "Byrne Wallace",
    "Byrne Wallace Shields" and "Byrne Wallace Shields LLP" - and appeared three times in
    the results with the same advert. Names that plainly mean one employer are folded
    together, but only within an identical title and location, which is what keeps the
    test from merging genuinely different firms.
    """
    companies = dict(session.execute(select(Company.id, Company.name)).all())

    base: dict[int, tuple[str, str, str]] = {}
    for row in rows:
        base[row.id] = (
            normalize_company_name(companies.get(row.company_id, "")),
            canonical_title(row.title),
            location_bucket(row.is_dublin, row.is_remote, row.location_norm),
        )

    # Within one title and location, fold the company names that mean one employer onto
    # a single spelling, so every copy of the advert lands on the same key. Shortest
    # first, so "byrne wallace" is the name the longer variants collapse onto.
    spellings: dict[tuple[str, str], list[str]] = {}
    for name, title, place in base.values():
        spellings.setdefault((title, place), []).append(name)

    canonical: dict[tuple[str, str, str], str] = {}
    for advert, names in spellings.items():
        kept: list[str] = []
        for name in sorted(set(names), key=lambda n: (len(n.split()), n)):
            match = next((k for k in kept if same_employer(k, name)), None)
            if match is None:
                kept.append(name)
            canonical[(*advert, name)] = match or name

    return {
        job_id: (canonical[(title, place, name)], title, place)
        for job_id, (name, title, place) in base.items()
    }


_skills_preloaded = False


def _preload_advert_skills() -> None:
    """Take the skills the snapshot build already found, once per process.

    See `rank.preload_skills`. A database without the table - a local crawl database, or
    a snapshot from before it existed - costs only speed, never correctness.
    """
    global _skills_preloaded
    if _skills_preloaded:
        return
    _skills_preloaded = True
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "select advert_hash, skills from advert_skills"
            ).all()
    except Exception:  # noqa: BLE001 - absent table, or not SQLite at all
        return
    stored = dict(rows)
    fingerprint = stored.pop("fingerprint", "")
    rank.preload_skills(
        fingerprint, ((key, json.loads(value)) for key, value in stored.items())
    )


def _search_results(
    profile: dict,
    *,
    query: str | None = None,
    page: int = 1,
    applied_keys: set[str] | None = None,
    saved_keys: set[str] | None = None,
    show_applied: bool = False,
) -> dict:
    """Rank the active jobs against a profile and build the template context."""
    candidate = Candidate(
        skills=set(profile.get("skills") or []),
        fields=profile.get("fields") or [],
        seniority=profile.get("seniority"),
        # The CV body is deliberately not in the session; see MAX_SESSION_SKILLS. The
        # lexical signal comes from the skills and corpus terms derived from it.
        text="",
        corpus_terms=set(profile.get("corpus_terms") or []),
        # The stated years rank as well as filter. Eligibility alone let every role the
        # searcher was not disqualified from score identically on seniority, so a
        # one-year search ranked Staff roles first whenever their advert stated no
        # minimum. See Candidate.level.
        years=profile.get("years"),
    )

    _preload_advert_skills()
    with session_scope() as session:
        stmt = select(JobPosting).where(_is_offerable())
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
        grouping = _grouping_keys(session, rows)
        rows = _prefer_direct_sources(session, rows, grouping)
        advert_keys = {job_id: key_for(*parts) for job_id, parts in grouping.items()}

        # Jobs already applied to drop out entirely, which is the point: a list that
        # still offers them leaves the searcher wondering, days later, whether they
        # applied or not. The toggle brings them back for anyone who wants to re-read a
        # posting or undo a mis-click.
        if applied_keys and not show_applied:
            rows = [row for row in rows if advert_keys[row.id] not in applied_keys]

        # Eligibility filtering happens here rather than in SQL so the rule lives in
        # one place; the candidate set for a single city is small enough that it costs
        # nothing.
        candidate_years = profile.get("years")
        want_internships = bool(profile.get("internships_only"))
        want_graduate = bool(profile.get("graduate_only"))
        rows = [
            row
            for row in rows
            if matches_experience(
                job_min_years=row.min_years_required,
                job_is_internship=row.is_internship,
                job_is_graduate=row.is_graduate,
                candidate_years=candidate_years,
                want_internships=want_internships,
                want_graduate=want_graduate,
            )
        ]
        company_rows = session.execute(select(Company)).scalars().all()
        companies = {c.id: c.name for c in company_rows}
        domains = {c.id: _logo_domain(c.name, c.website) for c in company_rows}

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
                    "domain": domains.get(job.company_id, ""),
                    "score": entry.score,
                    "why": entry.explain(),
                    "is_new": has_history and bool(first_seen and first_seen >= cutoff),
                    # Not every employer publishes a posting date - Google, for one,
                    # does not - so the date we first saw it is offered as a fallback
                    # and labelled as such rather than passed off as the posting date.
                    "posted": job.posted_at.strftime("%d %b %Y") if job.posted_at else None,
                    "first_seen": first_seen.strftime("%d %b") if first_seen else "",
                    "experience": _experience_label(job),
                    # 0 = a field the searcher ticked, 1 = one hop away. The template
                    # rules off between the two so adjacent work is offered rather than
                    # passed off as what they asked for.
                    "tier": entry.tier,
                    "fallback": entry.fallback,
                    "stretch": entry.seniority_fit == "stretch",
                    # What an application is recorded against. Identifies the advert
                    # rather than the row, so every copy of it counts as the same job.
                    "advert_key": advert_keys[job.id],
                    "applied": advert_keys[job.id] in (applied_keys or set()),
                    # Saved jobs deliberately stay in the results. Applied ones leave;
                    # a saved one is still a live opening you are weighing up, and
                    # hiding it would defeat the point of saving it.
                    "saved": advert_keys[job.id] in (saved_keys or set()),
                }
            )

        # Relevance order comes for free from rank_jobs above. A date sort re-orders the
        # same eligible set instead: jobs with no usable date (neither a posted date nor
        # a first-seen fallback - vanishingly rare) sink to the end either way, since
        # there is nothing to confidently call oldest or newest about them.
        sort_mode = profile.get("sort") or DEFAULT_SORT
        # Tiers are a property of the relevance order. A date sort deliberately discards
        # that order, so the divider would fall in a meaningless place and is suppressed.
        tiered = sort_mode == DEFAULT_SORT
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
        "tiered": tiered,
        "fallback": bool(page_items and all(i["fallback"] for i in page_items)),
        "show_applied": show_applied,
        "applied_count": len(applied_keys or set()),
        "saved_count": len(saved_keys or set()),
        "skill_count": len(profile.get("skills") or []),
        # What the model made of the CV, offered back rather than acted on. Shown only
        # where it disagrees with the boxes, because agreeing with the searcher is not
        # news and a banner that always fires is a banner nobody reads.
        "cv_summary": profile.get("cv_summary") or "",
        "cv_fields": [
            {"key": key, "label": FIELDS[key].label}
            for key in (profile.get("cv_fields") or []) if key in FIELDS
        ],
        "cv_fields_differ": bool(
            set(profile.get("cv_fields") or []) - set(profile.get("fields") or [])
        ),
        "candidate_years": profile.get("years"),
        "internships_only": bool(profile.get("internships_only")),
        "graduate_only": bool(profile.get("graduate_only")),
    }


# --------------------------------------------------------------------- accounts


def _safe_next(value: str | None) -> str:
    """Where to send someone after they sign in.

    Only a path on this site is ever accepted. An attacker who can get a visitor to
    follow `/login?next=https://evil.example` would otherwise have this site's own
    sign-in page hand them off to theirs, with the trust of our domain behind it.
    A protocol-relative `//evil.example` is the same attack with the scheme left off,
    which is why the check is on the parsed result rather than on the first character.
    """
    if not value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return "/"
    return value


# Why sign-in was asked for, when it interrupted an Apply or a Save. It only changes
# the heading to "One step first."; an explanation box above the Google button was
# judged unnecessary, and the lede already says what an account is for.
REASONS = frozenset({"apply", "save"})


def _auth_page(request: Request, *, mode: str, error: str = "", notice: str = "",
               email: str = "", status: int = 200, next_to: str = "/",
               reason: str = ""):
    context = _base_context(request)
    context.update(
        mode=mode, error=error, notice=notice, email=email, next_to=next_to,
        reason=reason in REASONS,
        google_enabled=bool(supabase.providers().get("google")),
        # The email form stays folded away behind Google unless it is the only way in,
        # or the person was already using it: an error or a typed address mean they are
        # mid-way through it, and folding it away would lose their place.
        email_open=bool(error or email or notice),
    )
    return templates.TemplateResponse(request, "account.html", context, status_code=status)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", why: str = ""):
    # Both POST handlers 404 without Supabase, and the header hides the sign-in link,
    # so rendering the form here handed anyone who arrived by bookmark, search result
    # or typed URL a page that looked completely functional and 404'd on submit. When
    # accounts are switched off, `/login` does not exist - which is what the gate in
    # `_account` already assumes.
    if not supabase.configured():
        raise HTTPException(status_code=404)
    if _account(request):
        return RedirectResponse("/profile", status_code=303)
    return _auth_page(request, mode="login", next_to=_safe_next(next), reason=why)


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, next: str = "/", why: str = ""):
    if not supabase.configured():
        raise HTTPException(status_code=404)
    if _account(request):
        return RedirectResponse("/profile", status_code=303)
    return _auth_page(request, mode="signup", next_to=_safe_next(next), reason=why)


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not supabase.configured():
        raise HTTPException(status_code=404)
    try:
        account = supabase.sign_in(email.strip(), password)
    except supabase.SupabaseError as exc:
        return _auth_page(request, mode="login", error=str(exc), email=email, status=401,
                          next_to=_safe_next(next))
    except httpx.HTTPError:
        logger.warning("supabase unreachable during sign-in", exc_info=True)
        return _auth_page(
            request,
            mode="login",
            error="Could not reach the accounts service. Please try again.",
            email=email,
            status=503,
            next_to=_safe_next(next),
        )

    _start_fresh(request)
    _remember_account(request, account)
    # Back to whatever they were looking at when they were asked to sign in, so the
    # Apply they clicked is one click away rather than a search away.
    return RedirectResponse(_safe_next(next), status_code=303)


@app.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not supabase.configured():
        raise HTTPException(status_code=404)
    if len(password) < 8:
        return _auth_page(
            request,
            mode="signup",
            error="Please use a password of at least 8 characters.",
            email=email,
            status=422,
            next_to=_safe_next(next),
        )
    try:
        account = supabase.sign_up(email.strip(), password)
    except supabase.SupabaseError as exc:
        return _auth_page(request, mode="signup", error=str(exc), email=email, status=422,
                          next_to=_safe_next(next))
    except httpx.HTTPError:
        logger.warning("supabase unreachable during sign-up", exc_info=True)
        return _auth_page(
            request,
            mode="signup",
            error="Could not reach the accounts service. Please try again.",
            email=email,
            status=503,
            next_to=_safe_next(next),
        )

    # No session comes back when the project asks people to confirm their address. Say
    # so, rather than showing a signed-out page that looks like the sign-up failed.
    if account is None:
        return _auth_page(
            request,
            mode="login",
            notice="Account created. Check your email for a confirmation link, then sign in.",
            email=email,
            next_to=_safe_next(next),
        )

    _start_fresh(request)
    _remember_account(request, account)
    return RedirectResponse(_safe_next(next), status_code=303)


def _site_origin(request: Request) -> str:
    """This site's own origin, as the browser sees it.

    Behind Vercel's proxy the request can arrive as plain http, and a callback address
    Supabase does not recognise is silently replaced with the project's Site URL - so
    the public deployment always names https.
    """
    origin = str(request.base_url).rstrip("/")
    if PUBLIC_DEPLOYMENT and origin.startswith("http://"):
        origin = "https://" + origin[len("http://"):]
    return origin


@app.get("/auth/google")
def google_sign_in(request: Request, next: str = "/"):
    """Send the visitor to Google, by way of Supabase."""
    if not supabase.configured():
        raise HTTPException(status_code=404)
    verifier, challenge = supabase.pkce_pair()
    # Small, and only until they come back: the verifier proves the code that returns
    # was asked for by this browser, and `next` returns them to what they were doing.
    request.session["oauth"] = {"verifier": verifier, "next": _safe_next(next)}
    url = supabase.google_authorize_url(f"{_site_origin(request)}/auth/callback", challenge)
    return RedirectResponse(url, status_code=303)


@app.get("/auth/callback")
def google_callback(
    request: Request,
    code: str = "",
    error: str = "",
    error_description: str = "",
):
    if not supabase.configured():
        raise HTTPException(status_code=404)
    pending = request.session.pop("oauth", None) or {}
    next_to = _safe_next(pending.get("next"))

    if error or not code:
        # Pressing Cancel on Google's screen lands here too; that is not worth an alarm.
        message = (
            "Google sign-in was cancelled."
            if error == "access_denied"
            else (error_description or "Google sign-in did not complete. Please try again.")
        )
        return _auth_page(request, mode="login", error=message, status=400, next_to=next_to)
    if not pending.get("verifier"):
        # A code with no verifier is a callback this browser never started - an old tab,
        # or cookies cleared on the way. Start again rather than guessing.
        return _auth_page(
            request, mode="login", status=400, next_to=next_to,
            error="That sign-in link has expired. Please try again.",
        )

    try:
        account = supabase.exchange_code(code, pending["verifier"])
    except supabase.SupabaseError as exc:
        return _auth_page(request, mode="login", error=str(exc), status=401, next_to=next_to)
    except httpx.HTTPError:
        logger.warning("supabase unreachable during google sign-in", exc_info=True)
        return _auth_page(
            request, mode="login", status=503, next_to=next_to,
            error="Could not reach the accounts service. Please try again.",
        )

    _start_fresh(request)
    _remember_account(request, account)
    return RedirectResponse(next_to, status_code=303)


@app.post("/logout")
def logout(request: Request):
    account = _account(request)
    if account is not None:
        supabase.sign_out(account)
    _forget_account(request)
    _start_fresh(request)
    return RedirectResponse("/", status_code=303)


PROFILE_TABS = ("saved", "applied")


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, tab: str = "saved"):
    """The signed-in person's own page: who they are, what they saved, what they applied
    to, and the way out. Saved and applied used to be two pages in the menu bar; they
    are two halves of one record, so they live together here."""
    account = _account(request)
    context = _base_context(request)
    context["tab"] = tab if tab in PROFILE_TABS else "saved"
    if account is None:
        context["error"] = "Sign in to see your profile."
        return templates.TemplateResponse(
            request, "profile.html", context | {"saved": [], "applications": []},
            status_code=401,
        )

    def fetch(call):
        try:
            return call(account), None
        except (supabase.SupabaseError, httpx.HTTPError) as exc:
            logger.warning("could not load a profile list", exc_info=True)
            return [], exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved_job = pool.submit(fetch, supabase.list_saved)
        applied_job = pool.submit(fetch, supabase.list_applications)
        saved_rows, saved_error = saved_job.result()
        applied_rows, applied_error = applied_job.result()

    if saved_error or applied_error:
        context["error"] = f"Some of your lists could not be loaded: {saved_error or applied_error}"

    applied_keys = {row.get("advert_key") for row in applied_rows}
    for row in saved_rows:
        row["saved_on"] = _format_applied_at(row.get("saved_at"))
        # A saved job that has since been applied to should say so rather than offering
        # Apply again, which is the confusion this whole feature exists to remove.
        row["applied"] = row.get("advert_key") in applied_keys
    for row in applied_rows:
        row["applied_on"] = _format_applied_at(row.get("applied_at"))

    search = _profile(request) or {}
    context.update(
        joined_on=_format_applied_at(account.joined),
        saved=saved_rows,
        applications=applied_rows,
        search_fields=[FIELDS[k].label for k in search.get("fields") or [] if k in FIELDS],
        search_years=search.get("years"),
    )
    return templates.TemplateResponse(request, "profile.html", context)


@app.get("/applications")
def applications(request: Request):
    """Now a tab of the profile; kept so bookmarks and old links still land."""
    return RedirectResponse("/profile?tab=applied", status_code=303)


def _format_applied_at(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return ""


@app.post("/applications", response_class=HTMLResponse)
def mark_applied(
    request: Request,
    advert_key: str = Form(...),
    title: str = Form(""),
    company: str = Form(""),
    url: str = Form(""),
):
    """Record an Apply click.

    The response is the little "Marked / Undo" control that replaces the button, so the
    row stays put until the next search rather than vanishing under the cursor of
    someone who has just clicked it.
    """
    account = _account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to track applications.")
    try:
        supabase.add_application(
            account, advert_key=advert_key, title=title, company=company, url=url
        )
    except (supabase.SupabaseError, httpx.HTTPError) as exc:
        logger.warning("could not record an application", exc_info=True)
        return HTMLResponse(
            f'<span class="muted small" title="{exc}">could not save</span>',
            status_code=502,
        )

    return templates.TemplateResponse(
        request,
        "_applied_tag.html",
        {"request": request, "advert_key": advert_key, "title": title,
         "company": company, "url": url},
    )


@app.post("/applications/remove", response_class=HTMLResponse)
def unmark_applied(
    request: Request,
    advert_key: str = Form(...),
    title: str = Form(""),
    company: str = Form(""),
    url: str = Form(""),
):
    account = _account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to track applications.")
    try:
        supabase.remove_application(account, advert_key)
    except (supabase.SupabaseError, httpx.HTTPError):
        logger.warning("could not remove an application", exc_info=True)
        return HTMLResponse('<span class="muted small">could not undo</span>', status_code=502)

    if request.headers.get("HX-Request"):
        # On the profile the row itself is the target, and an empty answer removes it.
        # In the results the control swaps back to a live Apply button.
        if request.headers.get("X-From") == "profile":
            return HTMLResponse("")
        if url:
            return templates.TemplateResponse(
                request,
                "_apply_button.html",
                {"request": request, "account": account, "advert_key": advert_key,
                 "title": title, "company": company, "url": url},
            )
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return RedirectResponse("/profile?tab=applied", status_code=303)


# ------------------------------------------------------------------- saved jobs


@app.get("/saved")
def saved(request: Request):
    """Now a tab of the profile; kept so bookmarks and old links still land."""
    return RedirectResponse("/profile?tab=saved", status_code=303)


@app.post("/saved", response_class=HTMLResponse)
def mark_saved(
    request: Request,
    advert_key: str = Form(...),
    title: str = Form(""),
    company: str = Form(""),
    url: str = Form(""),
):
    """Save a job for later. The response is the toggled control for that record."""
    account = _account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to save jobs.")
    try:
        supabase.add_saved(
            account, advert_key=advert_key, title=title, company=company, url=url
        )
    except (supabase.SupabaseError, httpx.HTTPError) as exc:
        logger.warning("could not save a job", exc_info=True)
        return HTMLResponse(
            f'<span class="accession" title="{exc}">could not save</span>',
            status_code=502,
        )

    return templates.TemplateResponse(
        request,
        "_saved_tag.html",
        {"request": request, "advert_key": advert_key, "saved": True,
         "title": title, "company": company, "url": url},
    )


@app.post("/saved/remove", response_class=HTMLResponse)
def unmark_saved(
    request: Request,
    advert_key: str = Form(...),
    title: str = Form(""),
    company: str = Form(""),
    url: str = Form(""),
):
    account = _account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to save jobs.")
    try:
        supabase.remove_saved(account, advert_key)
    except (supabase.SupabaseError, httpx.HTTPError):
        logger.warning("could not unsave a job", exc_info=True)
        return HTMLResponse(
            '<span class="accession">could not remove</span>', status_code=502
        )

    # From the profile the whole row should go; from the results the control just flips
    # back to an empty Save, so the record stays where the cursor left it.
    if request.headers.get("HX-Request") and request.headers.get("X-From") == "profile":
        return HTMLResponse("")
    if not request.headers.get("HX-Request"):
        return RedirectResponse("/profile?tab=saved", status_code=303)
    return templates.TemplateResponse(
        request,
        "_saved_tag.html",
        {"request": request, "advert_key": advert_key, "saved": False,
         "title": title, "company": company, "url": url},
    )


@app.post("/reset")
def reset(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", _base_context(request))


# ------------------------------------------------------------------- companies


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request):
    """Every company hiring in Dublin, the biggest shown as app icons, and the
    platforms their jobs are read from."""
    with session_scope() as session:
        employers = _hiring_employers(session)
        dublin = sum(e["jobs"] for e in employers)

        per_platform = session.execute(
            select(
                Source.adapter,
                func.count(JobPosting.id),
                func.count(func.distinct(JobPosting.company_id)),
            )
            .join(Source, Source.id == JobPosting.source_id)
            .where(_is_offerable(), JobPosting.is_dublin.is_(True))
            .group_by(Source.adapter)
        ).all()

    # Two adapters read plain careers sites; to a job seeker they are one thing.
    platforms: dict[str, dict] = {}
    for adapter, jobs, n_companies in per_platform:
        label, domain = PLATFORMS.get(adapter, (adapter, ""))
        entry = platforms.setdefault(
            label, {"label": label, "domain": domain, "jobs": 0, "companies": 0}
        )
        entry["jobs"] += jobs
        entry["companies"] += n_companies
    platforms_sorted = sorted(platforms.values(), key=lambda p: -p["jobs"])

    hiring_names = {e["name"] for e in employers}
    context = _base_context(request)
    context.update(
        employers=employers,
        top=[e for e in employers if e["domain"]][:36],
        dublin_count=dublin,
        employers_hiring=len(employers),
        platforms=platforms_sorted,
        global_names=[n for n in GLOBAL_NAMES if n in hiring_names],
        irish_names=[n for n in IRISH_NAMES if n in hiring_names],
        public_sector=sum(1 for e in employers if "publicjobs" in e["adapters"]),
        graduate_employers=sum(1 for e in employers if "gradireland" in e["adapters"]),
    )
    return templates.TemplateResponse(request, "companies.html", context)


@app.get("/companies/{company_id}/jobs", response_class=HTMLResponse)
def company_jobs(request: Request, company_id: int):
    """One company's open Dublin jobs, for the window that opens from its icon."""
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status_code=404)
        jobs = session.scalars(
            select(JobPosting)
            .where(
                _is_offerable(),
                JobPosting.is_dublin.is_(True),
                JobPosting.company_id == company_id,
            )
            .order_by(JobPosting.first_seen_at.desc())
        ).all()
        rows = [
            {
                "title": job.title,
                "url": job.url,
                "location": job.location_raw or "Dublin",
                "experience": _experience_label(job),
            }
            for job in jobs
        ]
        name = company.name
        domain = _logo_domain(company.name, company.website)
    return templates.TemplateResponse(
        request,
        "_company_jobs.html",
        {"request": request, "name": name, "domain": domain, "jobs": rows},
    )
