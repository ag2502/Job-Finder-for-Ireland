"""Location normalization.

Job boards store location as unnormalized free text. A single company's board was
observed carrying eleven distinct spellings of the same city:

    "Dublin, Ireland"   "Dublin"   "Dublin "   "London OR Dublin"   "Dublin OR London"
    "Dublin, London"    "Singapore, Dublin"    "SF, New York, Seattle, Dublin, Luxembourg"

so a `WHERE location = 'Dublin'` filter silently loses most matching roles.

The other half of the problem is that Dublin is not unique. Dublin, California and
Dublin, Ohio are real places with real technology jobs, and naive substring matching
imports them as Irish roles.

The rule that separates them is positional: in "Dublin, CA" the state token directly
follows the city, whereas in "SF, New York, Seattle, Dublin, Luxembourg" Dublin stands
as its own entry in a list of offices. So a US state is disqualifying only when it
immediately follows Dublin — not merely when it appears somewhere in the string.

`location_raw` is never modified by any of this. Normalization is purely additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# US states that actually have a Dublin, plus the rest, so any "Dublin, <state>" pair
# is caught. Two-letter codes and full names both appear in the wild.
_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
}

# "or" is a separator in "Dublin OR London" far more often than it is Oregon, and
# "in"/"me"/"la" collide with common words. Excluding them from the immediately-after
# check costs nothing: Dublin, Oregon is not a place.
_AMBIGUOUS_CODES = {"or", "in", "me", "la", "de", "ok", "hi", "id", "ma", "pa"}
_SAFE_STATE_CODES = _US_STATE_CODES - _AMBIGUOUS_CODES

_IRELAND_SIGNALS = {
    "ireland", "ie", "irl", "eire", "éire", "republic of ireland", "roi",
}

_US_SIGNALS = {
    "united states", "usa", "u.s.", "u.s.a.", "us",
}

# Dublin neighbourhoods, business districts and satellite towns that appear as the
# whole location string with no city name attached.
_DUBLIN_LOCALITIES = {
    "grand canal dock", "grand canal", "docklands", "silicon docks", "ifsc",
    "sandyford", "citywest", "city west", "blanchardstown", "leopardstown",
    "ballsbridge", "swords", "tallaght", "clonskeagh", "dun laoghaire",
    "dún laoghaire", "santry", "parkwest", "park west", "east point", "eastpoint",
    "cherrywood", "carrickmines", "lucan", "dundrum", "rathfarnham", "finglas",
    "ballycoolin", "damastown", "clondalkin", "malahide", "howth", "blackrock",
    "stillorgan", "booterstown", "ranelagh", "rathmines", "smithfield",
    "the liberties", "temple bar", "spencer dock", "north wall", "ringsend",
}

_REMOTE_PATTERNS = re.compile(
    r"\b(remote|work from home|wfh|anywhere|distributed|home[- ]based)\b", re.IGNORECASE
)
_HYBRID_PATTERN = re.compile(r"\bhybrid\b", re.IGNORECASE)

_DUBLIN_WORD = re.compile(r"\bdublin\b", re.IGNORECASE)

# Eircode routing keys for Dublin (D01-D24), and "Dublin 2" / "Dublin 6W" style.
_DUBLIN_POSTAL = re.compile(r"\b(?:d(?:0[1-9]|1[0-9]|2[0-4])|dublin\s?\d{1,2}w?)\b", re.IGNORECASE)

# Splits a multi-location string into individual place entries.
_SEPARATORS = re.compile(r"\s*(?:[,/|;&]|\bor\b|\band\b|\bplus\b)\s*", re.IGNORECASE)


@dataclass
class LocationResult:
    """Outcome of normalizing one raw location string."""

    raw: str | None
    is_dublin: bool = False
    is_remote: bool = False
    is_hybrid: bool = False
    needs_review: bool = False
    location_norm: str | None = None
    tokens: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.is_dublin


def _tokenize(text: str) -> list[str]:
    parts = _SEPARATORS.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _dublin_followed_by_us_state(text: str) -> bool:
    """True when a US state token directly follows a Dublin mention.

    This is what distinguishes "Dublin, CA" (a US city) from
    "SF, New York, Seattle, Dublin, Luxembourg" (a list of offices including Dublin).
    """
    for match in _DUBLIN_WORD.finditer(text):
        tail = text[match.end():].lstrip()
        if not tail.startswith(","):
            # Require a comma; "Dublin California" without one does not occur in
            # practice and the looser rule would misfire on prose.
            continue
        tail = tail[1:].strip().lower()
        if not tail:
            continue
        # Compare against the next entry only, up to the following separator.
        next_entry = _SEPARATORS.split(tail)[0].strip()
        if next_entry in _SAFE_STATE_CODES or next_entry in _US_STATE_NAMES:
            return True
    return False


def normalize_location(
    raw: str | None,
    *,
    company_is_irish: bool | None = None,
) -> LocationResult:
    """Normalize a free-text location.

    `company_is_irish` is an optional registry hint used only to break ties on a bare
    "Dublin" when the string carries a conflicting US signal.
    """
    result = LocationResult(raw=raw)
    if not raw or not raw.strip():
        return result

    text = re.sub(r"\s+", " ", raw).strip()
    lowered = text.lower()

    result.is_remote = bool(_REMOTE_PATTERNS.search(text))
    result.is_hybrid = bool(_HYBRID_PATTERN.search(text))

    tokens = _tokenize(text)
    result.tokens = tokens
    lowered_tokens = {t.lower() for t in tokens}

    has_dublin_word = bool(_DUBLIN_WORD.search(text))
    has_dublin_postal = bool(_DUBLIN_POSTAL.search(text))
    has_locality = any(t in _DUBLIN_LOCALITIES for t in lowered_tokens)
    # A locality can also be embedded rather than a standalone token, e.g.
    # "Grand Canal Dock, Dublin".
    if not has_locality:
        has_locality = any(loc in lowered for loc in _DUBLIN_LOCALITIES)

    has_ireland_signal = bool(lowered_tokens & _IRELAND_SIGNALS)
    has_us_signal = bool(lowered_tokens & _US_SIGNALS)

    if not (has_dublin_word or has_dublin_postal or has_locality):
        # Not a Dublin string at all. Still record a tidied form for other filters.
        result.location_norm = ", ".join(tokens) if tokens else text
        return result

    # A Dublin mention immediately followed by a US state is a US Dublin, unless the
    # string also explicitly names Ireland (a genuine multi-office listing).
    if _dublin_followed_by_us_state(text) and not has_ireland_signal:
        result.is_dublin = False
        result.location_norm = ", ".join(tokens) if tokens else text
        return result

    result.is_dublin = True
    result.location_norm = "Dublin, Ireland"

    # Flag for human review only where the evidence genuinely conflicts: a US country
    # signal with nothing Irish to anchor it. Keeping this narrow keeps the queue
    # meaningful rather than flooding it with every bare "Dublin".
    if has_us_signal and not (has_ireland_signal or has_dublin_postal or has_locality):
        if company_is_irish is not True:
            result.needs_review = True

    return result


def is_dublin(raw: str | None) -> bool:
    """Convenience wrapper for call sites that only need the boolean."""
    return normalize_location(raw).is_dublin
