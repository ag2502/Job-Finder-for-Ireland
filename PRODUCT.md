# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences served by one funnel, confirmed 2026-09-22:

- **Early-career** — students, new graduates and first/second-job searchers. The
  existing `Internships only` and `Graduate programmes only` filters exist for them.
- **Experienced professionals** — people already working who are looking to move, and
  who care about completeness and speed rather than guidance.

Both arrive doing the same job: *find out what is actually open in Ireland right now,
without visiting twenty different sites to do it.* Neither group is the secondary one;
the early-career path must be visible rather than hidden behind a checkbox.

## Product Purpose

Every live opening in one place, so nothing is missed and nobody has to hunt across
platforms. The searcher states the fields they want to work in and receives every
currently-active matching role in Ireland, ranked against the CV they added once to their
profile if they have one.

Success is that a searcher can stop checking other sites, because anything they would
have found there is already here.

## Positioning

The list is assembled from **employers' own careers systems**, not from job boards, and
it is **cumulative and stateful**: jobs are never deleted and never wholesale-replaced.
A role that was open yesterday is still listed today unless it was confirmed closed
twice.

Three rules make that true and are the defensible mechanism:

1. A failed crawl never closes jobs — absence only counts if the source was reached.
2. A suspiciously small result set is treated as failure, catching broken paginators
   that return *some* data and look successful.
3. Closing a role requires confirmed absence on two separate crawls.

Verified: 28 sources forced to return HTTP 503 across three consecutive crawls closed
**zero** of 3,736 jobs.

## Operating Context

- Searchers arrive mid-hunt, often repeatedly over weeks, frequently on a phone.
- The crawl runs every six hours via GitHub Actions, which also deploys the site.
- Applying always happens on the employer's own posting, in a new tab. This site never
  takes an application.

## Capabilities and Constraints

**Has today:**

- Field selection (required — at least one), years-of-experience filter, title filter,
  remote toggle, internships-only and graduate-only filters.
- A profile (2026-09-25) where a signed-in searcher adds their CV once, replaces or
  removes it, and saves what they are looking for. The finder ranks against the stored
  CV (switchable per search), lists the fields the CV points to in their own band below
  the chosen ones, and offers the saved details with one "Use my profile" button rather
  than filling the form in: a plain visit to the finder still starts blank, while a
  search link (the address a search writes, 2026-09-27) reopens that search.
- Ranking against a vocabulary learned from the job corpus, not a hardcoded skill list.
- What an advert states in its own prose (2026-09-27): the salary (146 of 1,048 Dublin
  adverts) and the work mode (311), read by rules that need a pay word or a working
  pattern word beside the figure, so revenue, funding and "hybrid cloud" never count.
  Unstated reads "Not stated"; nothing is estimated.
- New since your last visit (2026-09-27): for a signed-in searcher, "new" means found
  after their previous visit (a gap of an hour starts a new one), with a "show only
  these" toggle; signed out, it still means found in the last day.
- Optional accounts (Supabase, EU) that record applied-to jobs and exclude them from
  future results. A job counts as applied to only when the searcher says so (2026-09-28):
  Apply opens the employer's posting, and the site asks "Did you apply?" when they come
  back, since pressing Apply is not applying.
- An employer directory covering the whole registry, including employers that cannot be
  crawled — those link out to their own careers page rather than being hidden.

**Does not have, and must not be claimed:**

- **No AI writing: no CV tailoring, no cover letters, no advert summaries.** All three
  were built (2026-09-25 to 27) and removed on 2026-09-28 at the owner's request, and
  nothing on the site sends a CV or an advert to Gemini, Groq or any writing model. Apply
  goes straight to the employer. Copy must never promise a tailored CV or a letter.

- **No notifications beyond the opt-in email alerts.** Changed 2026-09-27 at the owner's
  request: a signed-in searcher may tick, on their profile, email alerts for new
  internships, new graduate programmes, or new jobs in the fields and years they saved,
  daily or weekly. Nothing is sent for anything not ticked, an email is sent only when
  something new has opened, and every email has a one-click unsubscribe. There are no
  push notifications, no in-app bell or unread counts, and no marketing email; copy must
  never imply otherwise.
- No applications taken on-site, no employer accounts, no pricing or payments.

**Hard constraints:**

- Stack is FastAPI + Jinja2 templates + HTMX, deployed as a Python serverless function
  on Vercel. There is no Node toolchain on the development machine, so React component
  libraries and any build step requiring npm are unavailable. UI work is hand-authored
  CSS and vanilla JS inside the existing templates.
- The CV document is parsed in memory and never written to disk or database. Changed
  2026-09-25 at the owner's request: the *reading* of it (skills, fields, seniority,
  years, a one-line summary, file name and size) is now kept on the searcher's profile
  in Supabase so they upload once, and Remove deletes it. The document itself staying
  unstored held until CV tailoring (same day), when the file began to be kept in a
  private per-account bucket. Tailoring is gone (2026-09-28) but the file is still kept,
  so a CV can be read again when the reader improves, and Remove deletes it. What is
  still not negotiable: nothing is shared with employers, and every stored item is
  deletable by its owner.

## Brand Commitments

- **Name: Sorted Place** — chosen by the user 2026-09-22 over alternatives.
  "Sorted" carries both the slang sense (*you're sorted, it's handled*) and the literal
  one (1,048 roles sorted and ranked). "Place" carries the everything-in-one-place
  promise that is the product's whole reason to exist.
- Domain: `sortedplace.com` is registered by someone else. `sorted.place` is available
  and preferred — the TLD completes the name.
- Voice: plain, confident, no hype. The product's claims are unusually literal and
  checkable, so the copy should stay literal too.

## Evidence on Hand

Real, verified figures from the current snapshot (2026-09-22) — use these, never
round them up:

| Fact | Value |
|---|---|
| Live Dublin roles | 1,048 |
| Live roles, all locations | 1,846 |
| Employers with live Dublin roles | **48** |
| Employers in the registry | 618 |
| Crawlable sources | 109 |
| Live internships | 9 |
| Live graduate programmes | 6 |
| Remote-flagged roles | 800 |
| Largest employers | Amazon 205, Google 116, PwC 70, MongoDB 65, Accenture 65, Stripe 56, Intercom 44, Version 1 43 |

**Correction required at launch:** the current page says jobs are taken "straight from
618 employers' own careers systems". 618 is the size of the registry, not the number of
employers currently hiring — that number is 48. The honest and stronger claim is the
role count.

No testimonials, press, customers, case studies or usage numbers exist. None may be
invented.

## Product Principles

1. **Completeness over curation.** Other sites shortlist and hide; this one shows
   everything that matches and says why each row is there.
2. **Never claim what the data cannot back.** Every number on the page is queryable
   from the database, including the unflattering ones.
3. **The searcher's stated intent outranks their history.** A CV says what someone has
   done; the chosen fields say what they want next, and that is what is searched.
4. **Absence of evidence is not evidence of absence** — in the crawler and in the UI.
   An unstated salary, date or experience level is shown as unstated, never guessed.
5. **Applying happens at the employer.** This site's job ends at a correct, live link.

## Accessibility & Inclusion

- `prefers-reduced-motion` is already honoured and must remain honoured by any new
  motion.
- The audience includes people job-hunting on a phone during a commute; mobile is a
  primary surface, not an adaptation. The current results table is unusable at 390px
  (a 14,960px-tall page) and that is the single worst defect in the product.
