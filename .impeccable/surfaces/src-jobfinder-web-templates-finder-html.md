---
version: 1
slug: "src-jobfinder-web-templates-finder-html"
primary_target: "src/jobfinder/web/templates/finder.html"
related_targets: ["src/jobfinder/web/templates/base.html","src/jobfinder/web/templates/_results.html","src/jobfinder/web/templates/directory.html","src/jobfinder/web/templates/account.html","src/jobfinder/web/templates/applications.html","src/jobfinder/web/templates/privacy.html"]
---

Scope: the whole public site — finder (home), results, employer directory, applications,
sign-in/sign-up, privacy. Visitor mode: Persuade on entry, Operate from the first result
onward. One page has to sell in three seconds and then work for twenty minutes.

Audience: Dublin job seekers, early-career and experienced, in one funnel. Job: find out
what is actually open right now without checking eight sites. Action: pick fields, get the
complete ranked list, click through to the employer. Proof: 1,048 live roles, 48 employers
currently hiring, 109 sources, never-deleted register. Constraints: FastAPI + Jinja + HTMX,
no Node, no npm; CV never stored; no email alerts exist and none may be implied.

## Direction contract

THESIS: A register where nothing is ever thrown out. Every opening is accessioned, dated
and filed, and it stays filed until the crawler confirms twice that it is gone — so an
empty drawer means the job is really closed, not that a scraper broke. It refuses the
category's centred-hero-plus-search-bar arrangement, and it refuses the infinite feed:
this is a finite, countable, physically bounded collection, and the count is the headline.

OWN-WORLD: Oxidised steel drawer face (#2f383c) edge to edge as ground; manila card stock
(#d9caa6) for every record; typewriter black (#1a1714); rubber-stamp red (#b7332a) for
dates, counts and NEW; brass (#b08d3f) for the label holder and the rod; pale ledger green
(#cdd8c2) banding alternate rows. Components: cards with a ruled index line, tab dividers
with angled shoulders, a brass rod running the full column, stamped date blocks, a drawer
rail footer. Never cream, never parchment, never a serif display face.

STORY: The visitor understands in one viewport that this is a complete register of live
Dublin roles, not a feed and not a board. They believe it because the count is specific,
the sources are named, and every card carries the date it was accessioned. They pick the
tab dividers for the work they want and pull the drawer.

FIRST VIEWPORT: Full-bleed steel drawer face. Brass label holder across the top reads
DUBLIN · LIVE OPENINGS with the count ticking up to 1,048 on load. Below it, one manila
card stands proud of the drawer carrying the CV field, years and title filter. Behind that
card, the 24 role fields render as tab dividers with angled shoulders — selecting one
raises it. Primary action ("Pull the drawer") sits on the raised card, right of the tabs.
The brass rod runs down the left edge through every card below the fold.

FORM: The accession register / library card catalog. Candidate 6 of my ordered grounded
list, assigned by the roll. Seed key e020797c.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
