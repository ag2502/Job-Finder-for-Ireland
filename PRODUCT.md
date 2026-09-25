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

Both arrive doing the same job: *find out what is actually open in Dublin right now,
without visiting twenty different sites to do it.* Neither group is the secondary one;
the early-career path must be visible rather than hidden behind a checkbox.

## Product Purpose

Every live opening in one place, so nothing is missed and nobody has to hunt across
platforms. The searcher states the fields they want to work in and receives every
currently-active matching Dublin role, ranked against the CV they added once to their
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
  than filling the form in: every visit to the finder still starts blank.
- Ranking against a vocabulary learned from the job corpus, not a hardcoded skill list.
- Optional accounts (Supabase, EU) that record applied-to jobs and exclude them from
  future results.
- An employer directory covering the whole registry, including employers that cannot be
  crawled — those link out to their own careers page rather than being hidden.

**Does not have, and must not be claimed:**

- **No email alerts, no subscriptions, no notifications of any kind.** Nothing in the
  codebase sends mail. "Everything in one place" means the complete live list, not
  push notifications. Copy must never imply otherwise.
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
  unstored is still not negotiable.

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
