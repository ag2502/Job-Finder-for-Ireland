"""Sites this project deliberately never crawls.

These are the boards whose terms forbid automated access, or that block and enforce it
(see "On LinkedIn and Indeed" in the README, and `data/platform_catalogue.csv`, status
`not accessible`). Excluding them is a decision, not a gap, and it has to hold for every
reader that follows an arbitrary URL - the generic extractors and the browser - because
a company's recorded careers page can itself be one of these sites: The Irish Times'
points at RecruitIreland, which it owns, and LinkedIn's own careers page is on
linkedin.com.
"""

from __future__ import annotations

from urllib.parse import urlsplit

EXCLUDED_DOMAINS = frozenset(
    {
        "linkedin.com",
        "indeed.com",
        "indeed.ie",
        "glassdoor.com",
        "glassdoor.ie",
        "glassdoor.co.uk",
        "irishjobs.ie",
        "jobs.ie",
        "monster.com",
        "monster.ie",
        "monster.co.uk",
        "recruitireland.com",
        "totaljobs.com",
    }
)


def is_excluded(url: str) -> bool:
    """True if the URL is on, or under, a site that is never to be crawled."""
    host = urlsplit(url if "//" in url else f"https://{url}").netloc.lower().split(":")[0]
    return any(host == domain or host.endswith("." + domain) for domain in EXCLUDED_DOMAINS)


class ExcludedSite(PermissionError):
    """Raised instead of fetching a site on the exclusion list."""
