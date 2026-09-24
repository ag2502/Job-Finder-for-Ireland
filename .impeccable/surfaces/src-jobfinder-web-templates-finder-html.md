---
version: 1
slug: "src-jobfinder-web-templates-finder-html"
primary_target: "src/jobfinder/web/templates/finder.html"
related_targets: ["src/jobfinder/web/templates/base.html","src/jobfinder/web/templates/_results.html","src/jobfinder/web/templates/companies.html","src/jobfinder/web/templates/account.html","src/jobfinder/web/templates/applications.html","src/jobfinder/web/templates/saved.html","src/jobfinder/web/templates/privacy.html"]
---

Scope: the whole public site — finder (home), results, the new /companies page, saved,
applications, sign-in/sign-up, privacy. Visitor mode: Persuade on entry and on
/companies, Operate from the first result onward. One page has to sell in three seconds
and then work for twenty minutes.
Audience: Dublin job seekers, early-career and experienced, in one funnel, usually on a
laptop with twenty careers tabs open, or on a phone mid-commute. Job: find out what is
actually open right now without checking twenty sites. Action: pick fields, get the
complete ranked list, click through to the employer. Proof: live role count, companies
hiring, named companies with their real counts and logos, the platforms read.
Constraints: FastAPI + Jinja + HTMX, no Node, no npm; CV never stored; no email alerts
exist and none may be implied. User pinned heyclicky.com as the reference (2026-09-24)
and asked for animation, interactivity and a dedicated companies page with logos.
Later references (2026-09-24): family.co, rive.app, igloo.inc, lusion.co,
henryheffernan.com, framer.com, robin-noguier.com, mercury.com. Taken from them: a pinned
scroll story whose live window acts out each step (Mercury accordion + Framer product-in-
page), word-by-word heading reveals and scroll-scattered windows (Lusion/Framer), springy
tactile controls (Family/Rive), cross-page view transitions (Robin). WebGL 3D declined for
phone performance.

## Direction contract
THESIS: Job hunting is a messy desktop — twenty careers sites open in twenty windows.
Sorted Place is the one tidy window. The page stages that literally: real employers'
careers windows scattered and draggable, then sorted into one. It refuses the job-board
default of a centred search bar over a list, and the dark "serious register" it replaces.
OWN-WORLD: A light desktop: cool grey ground (#eceef2) under a dotted grid; white app
windows with a grey title bar, traffic lights and a mono filename; glossy aqua pill
buttons (#2b74f0); Finder tag colours as the field-group system; app-icon squircles with
real company favicons and red notification badges carrying job counts; name-tag and
highlighter stickers (#ffd84a) in a hand face; a menu bar with a live Dublin clock.
STORY: The visitor sees their own chaos — Amazon, Google, Stripe windows with real counts
— understands this is every one of them in one place, drags a window for fun, presses
"sort my desktop", watches the windows fly into one, and picks the work they want.
FIRST VIEWPORT: Menu bar across the top (wordmark, find jobs, companies, saved/applied,
live count and clock). Centre: huge headline "Every job in Dublin, sorted." (sentence case, not lowercase:
Dublin is a proper noun, and the account tests assert that wording)
with the live count underneath and two pills: "find my jobs" (aqua) and "see all N
companies". Around it, 7–9 draggable careers windows with logos and real Dublin counts,
a name-tag sticker and highlighter scribbles. Below the fold: a dock of the biggest
employers (links to /companies), a pinned three-step "how it works" story, then the
find-my-jobs window. Signature interactions: drag-and-throw windows, the sort, the story.
FORM: Tidy desktop (heyclicky-pinned desktop OS), my grounded pick #1; roll 7dca2cd2
assigned #3 (Leap-card/Luas world), overruled by the user's pinned reference.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
