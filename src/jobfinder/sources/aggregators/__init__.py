"""Tier 4: licensed job aggregators.

Every other tier reads an employer's own board. This one reads someone else's index of
many employers' boards, which is a different trade in every respect:

* **Breadth.** An aggregator reaches the long tail no registry will ever enumerate — the
  eight-person agency in Galway that no company list carries.
* **Quality.** Descriptions are truncated, the apply URL is a redirect, and the employer
  name is free text rather than an identity. A direct source is better wherever one
  exists, which is why aggregated postings carry a higher `Source.tier` and lose to
  direct sources when `dedup_key` groups them.
* **Terms.** These are licensed APIs with keys and rate limits, not public endpoints.

## On LinkedIn and Indeed

They are absent deliberately, not as an oversight. Neither offers a public jobs API for
this purpose: Indeed retired its Publisher API to new applicants and now grants access
only through employer and partner programmes, and LinkedIn's jobs data is available only
to formal partners while its terms prohibit scraping, which it also blocks technically
and enforces litigiously.

Losing them costs far less than it appears to. Both are overwhelmingly *syndication*
layers — the Dublin roles they list are, in the main, the same postings this project
already reads from Greenhouse, Workday, Ashby and the rest, usually several hours
earlier and always with the full description and the real apply URL. The aggregators
here (Adzuna, Jooble, Careerjet, EURES) cover the genuine remainder under terms that
permit it.

## Credentials

Each adapter reads its key from settings and reports FAILED when unset rather than
raising at import. An unconfigured aggregator therefore behaves exactly like an
unreachable one: it is recorded, it changes nothing, and no job is closed because of it.
"""

from __future__ import annotations
