# Job Finder — Dublin

A job portal for Dublin, Ireland. Upload a CV, choose the fields you want to work in,
and get every currently-active relevant opening — sourced from employers' own careers
systems rather than only from job boards.

## The core guarantee

The job list is **cumulative and stateful**. Run it today and again tomorrow, and
yesterday's still-open roles are still there alongside newly-discovered ones. Jobs are
never deleted and never wholesale-replaced; every crawl reconciles against existing state.

Three rules make that true:

1. **A failed crawl never closes jobs.** Absence only means something if the source was
   actually reached. A naive "replace today's set" design would silently empty the
   database during any upstream outage.
2. **A suspiciously small result set is treated as failure.** A broken paginator returns
   *some* data and looks successful; comparing against the previous run catches what a
   status code cannot.
3. **Closing requires confirmed absence twice**, which absorbs transient flakiness at
   the cost of at most one day of staleness.

Verified against the live database: 28 sources forced to return HTTP 503 across three
consecutive crawls closed **zero** of 3,736 jobs.

## Quick start

```bash
source job/bin/activate
uv pip install -e ".[dev]"

jobfinder seed          # load the company registry
jobfinder crawl         # fetch and reconcile every source
jobfinder stats         # what is in the database
jobfinder serve         # the web portal at http://127.0.0.1:8000

pytest                  # 106 tests
```

Other commands:

```bash
jobfinder jobs engineer          # search active Dublin roles from the terminal
jobfinder detect intercom.com    # find a company's ATS
jobfinder detect acme.ie --add   # ...and register it
jobfinder crawl --adapter workday
```

## Sources

| Tier | Adapter | Notes |
|---|---|---|
| 1 | `greenhouse` | Public board API |
| 1 | `ashby` | `secondaryLocations` expanded (per-posting) |
| 1 | `lever` | `allLocations` expanded (per-posting) |
| 1 | `workable` | Structured city/country, so location is unambiguous |
| 1 | `workday` | Compound slug `tenant:wdhost:site`; dominant among Dublin MNCs |
| 1b | `amazon` | Bespoke platform; `country=IRL` |
| 1b | `google` | No API, but the careers page is server-rendered |

`jobfinder detect` finds which of these a company uses, including companies that *embed*
a Greenhouse or Lever widget on their own domain — which turns an apparently
scrape-only site into a clean API source.

### One trap worth knowing

Greenhouse's `offices` field is the **company-wide** office list, not the posting's
location. Intercom tags every job with `['Dublin, Ireland', 'London, England']`, so
treating it as a per-job location marks every London role as Dublin. Ashby's
`secondaryLocations` and Lever's `allLocations` genuinely are per-posting. The adapters
treat them differently, and `tests/test_adapters.py` locks that in.

## Location normalization

Job boards store location as free text. One company's board carried **eleven** distinct
spellings of the same city — `Dublin, Ireland`, `Dublin`, `London OR Dublin`,
`SF, New York, Seattle, Dublin, Luxembourg`, plus trailing-space variants — so a
`WHERE location = 'Dublin'` filter loses most matching roles.

Dublin is also not unique: Dublin, California and Dublin, Ohio have real technology jobs.
The rule that separates them is positional — a US state is disqualifying only when it
*immediately follows* the city, so `Dublin, CA` is rejected while
`SF, New York, Seattle, Dublin, Luxembourg` is kept.

`location_raw` is never modified. Normalization is purely additive.

## Matching

Rule-based parsing and lexical ranking, chosen so the whole thing runs on free tiers:

- **Resume** → skills, seniority, years, titles. Inspectable rules, no API budget, and
  a miss is a visibly absent keyword rather than an untraceable hallucination.
- **Ranking** → skills overlap (40%), field match (30%), BM25 (20%), seniority (10%),
  with a recency multiplier. Every score explains itself.
- **Fields** → choosing "Data Engineering" also surfaces analytics engineering, BI and
  data science, expanded one hop through the taxonomy in `normalize/taxonomy.py`.

Sentence-transformer embeddings were deliberately not used: torch is ~2GB, which does
not fit the free tiers this targets, and job matching is dominated by exact technology
and title tokens where lexical scoring is strongest. `rank_jobs` takes rows and returns
scores, so swapping in embeddings later changes nothing else.

## Privacy

**The uploaded CV is never written to disk or to the database.** It is parsed in memory,
the derived signals go into a signed session cookie, and the document is discarded
before the response is sent.

That is the stronger design, not a shortcut. Under GDPR, storing a CV makes you a
controller with retention, access and erasure duties, and it becomes the most sensitive
asset in the system. Keeping the few hundred bytes matching actually needs removes that
liability while losing nothing a searcher would notice.

## Layout

```
src/jobfinder/
  core/       config, database, ORM models
  registry/   seed loading and ATS detection
  sources/    one adapter per platform; bespoke/ for own-platform employers
  normalize/  location, dedup, field taxonomy
  pipeline/   orchestration and the reconciliation state machine
  matching/   resume parsing and ranking
  web/        FastAPI + HTMX portal
```

## Deployment

The crawler runs on GitHub Actions (`.github/workflows/crawl.yml`) every six hours —
free on public repos, no server to maintain. Point `JOBFINDER_DATABASE_URL` at a hosted
Postgres (Neon's free tier) and the same code runs unchanged; SQLite is only the local
default. The workflow runs the test suite before crawling, so a broken reconciler can
never reach the production database.

## Not yet built

- SmartRecruiters, Recruitee, Teamtailor, Personio adapters
- Tier 1b: Microsoft, Apple, Meta (each needs a headless browser for token/JS handling)
- Tier 3 aggregators (Adzuna, Jooble, Careerjet) and Tier 4 (LinkedIn, Indeed)
- CRO register import for full company-universe accounting
- User accounts, saved searches, email digests
# Job-Finder-for-Ireland
# Job-Finder-for-Ireland
# Job-Finder-for-Ireland
# Job-Finder-for-Ireland
