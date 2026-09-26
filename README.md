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

jobfinder seed              # companies with a known ATS and slug
jobfinder import-universe   # the wider registry: name + website only
jobfinder detect-all        # resolve those to crawlable sources
jobfinder extract-blocked   # generic extraction for the ones with no readable ATS
jobfinder crawl             # fetch and reconcile every source that is due
jobfinder coverage          # how much of the registry is actually crawled
jobfinder serve             # the web portal at http://127.0.0.1:8000

pytest
```

Other commands:

```bash
jobfinder stats                  # what is in the database
jobfinder jobs engineer          # search active Dublin roles from the terminal
jobfinder detect intercom.com    # find one company's ATS
jobfinder detect acme.ie --add   # ...and register it
jobfinder crawl --adapter workday
jobfinder crawl --all            # ignore the staleness schedule
jobfinder detect-all --dry-run   # report findings without registering them
```

## Sources

Coverage is `registry size x detection hit rate`. Both terms matter, and for a long time
the first one was the binding constraint: seven adapters against a hand-curated list of
39 companies crawls 39 companies. The registry is now imported in bulk and resolved
automatically, and the adapters are grouped into four tiers by how close each sits to
the employer.

| Tier | Adapter | Notes |
|---|---|---|
| 1 | `greenhouse` | Public board API |
| 1 | `ashby` | `secondaryLocations` expanded (per-posting) |
| 1 | `lever` | `allLocations` expanded (per-posting) |
| 1 | `workable` | Structured city/country, so location is unambiguous |
| 1 | `workday` | Compound slug `tenant:wdhost:site`; dominant among Dublin MNCs |
| 1 | `smartrecruiters` | Native `country=ie` facet; list-then-detail for descriptions |
| 1 | `recruitee` | Full advert in the list response, so one request per board |
| 1 | `personio` | XML feed; tenant may live on `.de` or `.com` |
| 1 | `phenom` | Phenom career sites' search widget, filtered by the site's own Irish facet value |
| 1 | `corehr` | CoreHR e-recruitment tenants (the universities, much of the public sector) |
| 1 | `cornerstone` | Cornerstone career sites, through the regional API the page names |
| 1 | `rezoomo` | Irish recruitment platform; a company page's one-call job list |
| 1 | `hrmanager` | HR Manager job portal JSON list per customer |
| 1 | `taleo_tbe` | Taleo Business Edition career sections, following their scroll pages |
| 1 | `wordpress` | A WordPress site's vacancy post type, through the REST API |
| 1b | `amazon` | Bespoke platform; `country=IRL` |
| 1b | `google` | No API, but the careers page is server-rendered |
| 1b | `apple` | Search page carries its results as hydration data; `ireland-IRL` |
| 1b | `tiktok` | Public search API; city code `CT_37` is Dublin |
| 1b | `ibm` | IBM's site search API, filtered to Ireland |
| 1b | `revolut` | Every position is embedded in the careers page; Irish ones kept |
| 1b | `hse` | HSE job search, paged; confined competitions (staff only) left out |
| 3 | `jsonld` | Generic `schema.org/JobPosting` extraction from any careers site |
| 4 | `adzuna` | Licensed aggregator; one source carries many employers |

Tier 1 reads an employer's own board through a public API. Tier 3 targets a *convention*
rather than a platform — Google requires `JobPosting` structured data for a role to
appear in its jobs results, so a large share of employers publish machine-readable job
data no matter what software renders their site, and one extractor reaches employers on
Teamtailor, BambooHR, Occupop and bespoke WordPress careers pages alike. Tier 4 is the
long tail no registry will enumerate.

### Building the registry

```bash
jobfinder import-universe    # name + website only; ~470 Irish employers ship in data/
jobfinder detect-all         # resolve each one to a crawlable source
jobfinder extract-blocked    # try generic extraction on whatever detection could not read
jobfinder coverage           # what is crawled, what is not, and why
```

`import-universe` deliberately carries no `adapter` or `slug`. Curating a verified
platform and slug per company caps the registry at what a person can maintain by hand;
carrying only `name,website` scales to whatever list can be obtained, and detection does
the rest. A wrong domain is cheap and self-reporting: detection fails to reach it, the
company is marked `UNRESOLVED`, and it appears in `jobfinder coverage` as work to do.

### How detection finds a board

Four strategies, cheapest first, under a per-company wall-clock budget:

1. **The homepage**, which often carries the ATS fingerprint in a footer or hiring widget.
2. **The site's own careers links**, followed one hop further — a board is frequently one
   click *past* the careers page, since `stripe.com/careers` is a brochure whose "see
   open roles" button leads to the page that embeds Greenhouse.
3. **Careers subdomains and guessed paths**, for the many large employers whose menus are
   rendered client-side and therefore invisible to link discovery.
4. **Asking the boards directly.** Fingerprinting can only find an ATS a page admits to.
   Stripe *is* on Greenhouse and `boards-api.greenhouse.io/v1/boards/stripe` serves its
   jobs, but stripe.com never names Greenhouse in its markup. Probing each platform for a
   populated board under the company's own domain name settles that in one request, and
   a populated board is proof rather than inference.

Probing is inference from a name collision, so it has to be able to say no. Two rules
make it safe, and both were added after it registered `meta.recruitee.com` — a real,
populated board belonging to an unrelated company — as Meta's:

* **An empty board is absence, not a match.** An empty board and a wrong guess are the
  same response.
* **A board must be shown to belong to the company.** Where the platform names the
  board's owner, that name must agree; where it does not, only a slug distinctive enough
  to be unlikely to collide is accepted. That costs some genuine five-letter matches —
  Udemy is found by fingerprinting but no longer by probing — which is the right way
  round: missing an employer costs a sweep, whereas misattributing one files a
  stranger's vacancies under a household name and looks entirely legitimate to the
  searcher.

### Being missed vs. not being automated

No crawler will reach every Irish employer. What is achievable is that none is *silently*
absent, and `CoverageState` is the accounting that makes the difference visible:

| State | Meaning |
|---|---|
| `ats_detected` / `bespoke_adapter` / `generic_extraction` | Crawled |
| `blocked` | Careers page known, no ATS readable — the work queue |
| `no_careers_page` | Reached, genuinely has none |
| `unresolved` | Not yet checked, or unreachable |

`extract-blocked` works the `blocked` queue: it trial-extracts each careers page once and
registers a `jsonld` source only if that trial actually returns postings. Registering
speculatively would add hundreds of sources that fail every run until the circuit breaker
disables them, and would inflate the coverage number with companies the crawler cannot
really read — so coverage here means what it says.

Every company keeps its `careers_url` regardless, and `/directory` in the portal lists
all of them — live openings where they are crawled, a direct link to the employer's own
jobs page where they are not. A company we cannot crawl is one click away rather than
invisible.

### Scale

Concurrency is per host, not global. Twenty Greenhouse boards are twenty slugs on one
hostname, so a naive pool of sixteen workers would put sixteen simultaneous requests on
`boards-api.greenhouse.io` and none anywhere else. Each host gets its own slot and its
own courtesy delay, which is both faster and politer than the serial version was.
Fetching is parallel; reconciliation stays serial on one thread, so the state machine's
guarantees are exactly what they were.

High-priority companies are crawled every run; the long tail waits for
`stale_after_hours`. Re-fetching a twelve-person consultancy every six hours costs far
more than the freshness it buys, while the multinationals that carry most of the roles
stay current.

**Detection has to be able to give up.** Sweeping hundreds of unknown hosts means some
of them will hang in a way no HTTP timeout covers — name resolution above all, since
`getaddrinfo` is a blocking C call that ignores both `socket.setdefaulttimeout` and
httpx's timeouts. Such a worker cannot be cancelled, and `ThreadPoolExecutor` *joins* its
workers on exit, so one wedged lookup stops the whole sweep at the moment it tries to
finish. Three consecutive full sweeps of this registry died exactly that way. The sweep
now runs on daemon threads under a whole-run deadline: a stuck probe is abandoned rather
than waited on, and the company it belonged to keeps its state and is retried next run —
which is how an unreachable site should be treated anyway. Detection is layered
accordingly: a body-size cap, a per-request timeout, a read-loop deadline, a per-company
budget, and a whole-sweep deadline, each covering a failure the others do not.

### On LinkedIn, Indeed and Meta

Absent deliberately, not as an oversight. Neither offers a public jobs API for this
purpose: Indeed retired its Publisher API to new applicants and grants access only
through employer and partner programmes, and LinkedIn's jobs data is available only to
formal partners while its terms prohibit scraping, which it also blocks technically and
enforces litigiously.

Meta is excluded on the same grounds: the robots.txt of its careers site states that
automated collection is prohibited without Facebook's written permission.

Losing them costs less than it appears to. Both are overwhelmingly *syndication* layers —
the Dublin roles they carry are largely the same postings this project already reads from
Greenhouse, Workday, Ashby and the rest, usually earlier and always with the full
description and the real apply URL. Adzuna covers the genuine remainder under terms that
permit it, and the same posting arriving from both an aggregator and an employer's own
board is collapsed by `dedup_key`, with the direct source winning.

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
- **Fields** → 56 fields under 11 groups in `normalize/taxonomy.py`, from Machine
  Learning to Hospitality. Choosing "Data Engineering" also surfaces analytics
  engineering, BI and data science, expanded one hop out.

### How close is "related"?

One hop, but a weighted one. Every edge is `NEAR` or `FAR`, and the two are ranked into
separate bands rather than blended:

| Band | Meaning | Field score | Shown under |
|---|---|---|---|
| 0 | a field you ticked | 1.00 | (top of the list) |
| 1 | `NEAR` — the same craft under another name | 0.60 | Closely related roles |
| 2 | `FAR` — a plausible pivot, not the same job | 0.24 | A sideways move into another field |
| 3 | no field match, kept on skill overlap alone | 0.00 | Matched on your skills, not your fields |

The weighting is not cosmetic. Software Engineering is the largest bucket in the Dublin
corpus and neighbours half the taxonomy, so an unweighted edge into it drowns whatever
was actually ticked: picking **Machine Learning** returned a page of "Software Developer
Graduate" before Data Science got a look in. A `FAR` neighbour also keeps its vocabulary
out of the BM25 query, which was pulling the ranking the same way a second time.

### Reading the CV with a model

The rules above read a CV as a bag of keywords. A CV that says *"trained a transformer
on 40M product reviews"* contains no title line and never says "machine learning", so
the rules find a few tokens and no field at all.

`matching/llm_profile.py` optionally asks a model instead. The task is narrow — pick
fields from a closed list of 56 — so a free open-weight model is enough: the JSON schema
is generated from `FIELDS`, and everything returned is re-checked against the same
tables the ranker uses. It is classification into known labels, not open generation.

It is **entirely optional and off by default**. Any OpenAI-compatible endpoint works,
because the request is plain JSON over `httpx`, which the deployment already carries:

```bash
# Cerebras free tier: open-weight models, no card, ~1M tokens/day (≈285 CV reads)
export JOBFINDER_LLM_BASE_URL=https://api.cerebras.ai/v1
export JOBFINDER_LLM_MODEL=llama-3.3-70b
export JOBFINDER_LLM_API_KEY=csk-...

# Optional second endpoint, tried when the first rate-limits
export JOBFINDER_LLM_FALLBACK_BASE_URL=https://api.groq.com/openai/v1
export JOBFINDER_LLM_FALLBACK_MODEL=llama-3.3-70b-versatile
export JOBFINDER_LLM_FALLBACK_API_KEY=gsk_...
```

| Provider | Base URL | Model |
|---|---|---|
| Cerebras | `https://api.cerebras.ai/v1` | `llama-3.3-70b` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| OpenRouter | `https://openrouter.ai/api/v1` | `meta-llama/llama-3.3-70b-instruct:free` |
| Hugging Face | `https://router.huggingface.co/v1` | `Qwen/Qwen2.5-72B-Instruct` |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5:7b` (no key needed) |

Set nothing and the site behaves exactly as it did: `read_cv` returns `None` and the
rules carry the whole job. That fallback is what makes the site independent of any free
tier staying up — a spent daily allowance costs the quality of one CV reading, never a
search. The call happens once per upload, not per search: the CV goes up once, on the
profile, and every search reuses the reading stored there.

What the model reads never replaces the boxes you ticked. The fields it names come up in
a band of their own, "Where your CV points", below the chosen fields and above their
neighbours, because a CV records what someone has done and the boxes state what they
want to do next.

Sentence-transformer embeddings were deliberately not used: torch is ~2GB, which does
not fit the free tiers this targets, and job matching is dominated by exact technology
and title tokens where lexical scoring is strongest. `rank_jobs` takes rows and returns
scores, so swapping in embeddings later changes nothing else.

## Tailoring a CV to a job

Pressing **Apply** can first write a version of the searcher's CV for that advert. The
code is in `src/jobfinder/tailor/`:

- `document.py` reads the CV into paragraphs and writes new wording back into **a copy of
  the original file**, so every CV keeps its own layout. Word files are edited run by
  run; PDFs are edited in place with PyMuPDF, in the CV's own embedded fonts, with inline
  bold kept, columns detected, and the new text spliced into the page's content where the
  old text was so parsers read it in order. Name, contact details, job titles, dates,
  education and certificates are locked in code. A PDF line that would not fit is left as
  it was; Word and text CVs may drop an irrelevant bullet, since they reflow.
- `rewrite.py` asks a model for edits and then checks them: no figure the CV never stated,
  no edits to locked lines, at most a third of bullets removed. Every changed sentence is
  linted and proofread (`proofread.py`, LanguageTool).
- `llm.py` tries free models in order: Gemini Flash, Gemini Flash-Lite, then Groq.
- `ats.py` scores the finished file the way an applicant tracking system reads one:
  advert keywords, job title, standard sections, contact details, readability, measurable
  results and length. Deterministic, and every point has a stated reason.

The searcher reviews every change, suggests more in their own words as many times as they
like, then accepts, saves (under **Tailored CVs** on their profile, apart from the CV
searches use) and downloads it, and goes on to the posting. Setup: step 6a of
`docs/accounts-setup.md`. Tested against the layouts in `tests/fixtures/`.

This project is licensed AGPL-3.0 (see `LICENSE`), because PyMuPDF is.

## Privacy

A signed-in searcher adds their CV once, on their profile. The reading of it (skills, the
fields it points to, seniority, years, a one-line summary) is kept in the `profiles`
table and ranks every search; the file itself is kept in a private Supabase storage
bucket so tailoring can keep its layout. Both sit behind row level security that lets an
account reach only its own rows and its own folder, and **Remove** deletes both outright.

Tailoring sends the CV's text and the advert to the free model chain above, and the
changed sentences to LanguageTool. A Gemini key from an EU account keeps Google's
paid-service data terms on the free tier. The privacy page says all of this to searchers.

**Configuring a model endpoint changes this, and it is the one thing here that does.**
With `JOBFINDER_LLM_API_KEY` set, up to 24,000 characters of the CV are sent to that
provider on upload. Nothing is stored at either end by this application, but the
document does leave the machine, and whatever that provider logs is theirs. Three
consequences worth being deliberate about:

- Unset, none of this happens. The rules run locally and the CV never leaves the
  process, which is the default precisely so the privacy claim above holds by default.
- Pointing `JOBFINDER_LLM_BASE_URL` at a local Ollama keeps the whole thing on one
  machine, with no provider in the picture at all.
- A public deployment that sets a key is processing other people'"'"'s CVs through a third
  party, which belongs in a privacy notice before it is switched on.

## Layout

```
src/jobfinder/
  core/       config, database, ORM models
  registry/   seed loading, universe import, ATS detection, bulk sweep
  sources/    one adapter per platform
              bespoke/     employers on their own platform
              aggregators/ licensed third-party indexes
              jsonld.py    generic schema.org extraction
  normalize/  location, dedup, field taxonomy
  pipeline/   orchestration, concurrent fetching, reconciliation state machine
  matching/   resume parsing and ranking
  web/        FastAPI + HTMX portal
```

## Deployment

Two GitHub Actions workflows, split by how fast the data underneath each one moves.

| Workflow | Schedule | Does |
|---|---|---|
| `crawl.yml` | every 6 hours | tests, then fetch and reconcile every source that is due |
| `registry.yml` | weekly | import companies, detect their ATS, try generic extraction |

The split is the point. Detection sweeps a few hundred unknown hosts, takes 30-60
minutes, and typically registers a handful of new sources — because a company's website
and choice of ATS change on a scale of months, while its job postings change hourly.
Running detection every six hours spent most of the CI budget re-confirming what was
already known. Fetching earns a frequent schedule; growing the registry does not.

Neither workflow triggers the other. `registry.yml` registers sources with
`last_success_at = NULL`, which `select_sources` treats as always due, so the next crawl
collects them on its own. Both share one `concurrency` group, because the reconciler's
guarantees assume a single writer.

The crawl runs the test suite first and refuses to start without a database configured.
Both guards protect the same thing: a broken reconciler, or a run that silently writes
to a throwaway SQLite file in the container, would quietly break the cumulative-state
promise this project rests on.

### Setting it up

Point `JOBFINDER_DATABASE_URL` at hosted Postgres — Neon's free tier is ample — and the
same code runs unchanged; SQLite is only the local default. Add it under
**Settings > Secrets and variables > Actions**, then bootstrap the database once:

```bash
export JOBFINDER_DATABASE_URL=postgresql://...
jobfinder seed && jobfinder import-universe && jobfinder detect-all && jobfinder crawl
```

Two things worth knowing. GitHub disables scheduled workflows after 60 days without a
push, and its cron is best-effort — expect drift of minutes to tens of minutes, which
nothing here depends on. And the database is now the asset rather than the code: it
holds history that cannot be re-derived, since a job's original `first_seen_at` is gone
once lost. Turn on point-in-time restore.

## Not yet built

- **Oracle Recruiting Cloud, SuccessFactors, Taleo, Eightfold, iCIMS, Avature,
  Teamtailor.** Detection recognises and records all of these but cannot crawl them yet.
  Naming them is deliberate: `jobfinder coverage` can then say which missing adapter
  would buy the most coverage, instead of lumping them in with sites that have no
  careers page. The first sweep put Oracle Recruiting Cloud at the top of that list —
  BNY Mellon, JPMorgan and Dell all run it — so it is the highest-value adapter to
  write next. Companies on these platforms currently fall to the `jsonld` extractor.
- **Microsoft, Apple and Meta.** Each hides its board behind a CSRF token or a
  client-rendered shell, so each needs a headless browser rather than an HTTP client.
  They are in the registry and link out from the directory in the meantime.
- **Jooble, Careerjet and EURES adapters.** Adzuna establishes the aggregator tier;
  these are the same shape and mostly a matter of credentials.
- **Adzuna credentials.** The adapter is written and fails cleanly without a key
  (`JOBFINDER_ADZUNA_APP_ID` / `JOBFINDER_ADZUNA_APP_KEY`), so it has not been exercised
  against the live API.
- **CRO register import** for full company-universe accounting. The shipped list is
  ~470 employers assembled by hand; the register is the path past that.
- **On-demand deep check** — re-crawling one company live from the directory page.
- User accounts, saved searches, email digests
