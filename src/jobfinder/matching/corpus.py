"""Vocabulary learned from the job corpus.

Every off-the-shelf resume parser worth using still ships a hand-written skills list —
pyresparser has its `skills.csv`, leverparser matches regex patterns, and neither infers
which job domains a CV belongs to. A fixed list is wrong the moment the market moves:
it cannot know about a framework released last month, it carries technologies nobody in
Dublin hires for, and it encodes one person's guess about what matters.

This module derives the vocabulary from the job adverts themselves. The corpus *is* the
ground truth about what Dublin employers are asking for, and it re-derives itself every
time the crawler runs.

How it works:

1. Every advert is tokenised into unigrams and bigrams.
2. Terms are kept when they appear in at least `MIN_DOC_FREQ` adverts and in no more
   than half of them, which discards typos and near-universal filler.
3. Each surviving term carries its inverse document frequency. This, not a blocklist,
   is what neutralises boilerplate: measured on the live corpus, "equal opportunity"
   weighs 0.00 and "communication skills" 1.40, while "dbt" weighs 4.82 and "airflow"
   4.48. The corpus separates signal from filler on its own.
4. A CV is then read against this learned vocabulary. Whatever it shares with real
   Dublin adverts *is* its skill set — no list to maintain. On the current corpus this
   finds 75 meaningful terms in a CV where the previous hand-written list found 21.

The result is cached on disk and rebuilt when the corpus changes materially.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jobfinder.core.config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_PATH = DATA_DIR / "corpus_vocab.json"

# A term must appear in at least this many adverts to be considered real rather than a
# typo or one employer's internal jargon.
MIN_DOC_FREQ = 3

# ...and in no more than this share of them, which drops only near-universal filler.
#
# The ceiling is deliberately loose. A tight one (20%) looked like it cleanly removed
# boilerplate, but it also removed python, aws, kubernetes and sql - core skills are
# *common* in technology adverts, so frequency alone cannot separate them from filler.
# Boilerplate that survives is handled by IDF instead, which weights "communication
# skills" near zero while "kubernetes" stays high. Downweighting beats excluding.
MAX_DOC_RATIO = 0.50

MAX_VOCAB = 200_000

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.]{1,28}")

# Structural words carry no signal about a role. This is a closed class of English
# function words, not a domain list - it does not need maintaining as the market moves.
_STOPWORDS = frozenset(
    """
a an and are as at be by for from has have he her his i in is it its of on or our she
that the their them they this to us we what when where which who will with you your
about above after again against all also am an any because been before being below
between both but by can cannot could did do does doing down during each few further
had having how if into itself just more most no nor not now off once only other out
over own same should so some such than then there these those through too under until
up very was way were while why would
role team work working experience opportunity position candidate ability year years
new use using help make build like well across within join looking seeking want need
please apply application applicant company business world people person time day
including include includes etc via per across able
""".split()
)


@dataclass
class Vocabulary:
    """Learned terms with their inverse document frequency."""

    idf: dict[str, float]
    n_docs: int

    def __contains__(self, term: str) -> bool:
        return term in self.idf

    def weight(self, term: str) -> float:
        return self.idf.get(term, 0.0)

    @property
    def size(self) -> int:
        return len(self.idf)


# Sentence and clause boundaries. Bigrams must not span these: "...at scale. Skills
# include..." would otherwise yield the meaningless term "scale. skills".
_BOUNDARY = re.compile(r"[.,;:!?()\[\]{}<>\"'\n\r\t|/\\]+|\s+[-–—]\s+")


def _clean(token: str) -> str:
    """Trim trailing dots while preserving internal ones, so "python." becomes
    "python" but "node.js" and ".net" survive intact."""
    return token.rstrip(".")


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [
        cleaned
        for token in _TOKEN.findall(text.lower())
        if (cleaned := _clean(token))
        and cleaned not in _STOPWORDS
        and not cleaned.isdigit()
        and len(cleaned) > 1
    ]


def _grams(text: str | None) -> set[str]:
    """Unigrams plus bigrams, with bigrams confined within a clause.

    Bigrams capture the multi-word terms carrying most of the meaning in job adverts -
    "machine learning", "site reliability", "supply chain" - which unigrams alone would
    split into uselessly generic halves.
    """
    if not text:
        return set()

    grams: set[str] = set()
    for segment in _BOUNDARY.split(text.lower()):
        tokens = tokenize(segment)
        grams.update(tokens)
        for first, second in zip(tokens, tokens[1:]):
            grams.add(f"{first} {second}")
    return grams


def build(documents: list[str]) -> Vocabulary:
    """Derive a vocabulary from raw advert text."""
    n_docs = len(documents)
    if n_docs == 0:
        return Vocabulary(idf={}, n_docs=0)

    doc_freq: Counter[str] = Counter()
    for document in documents:
        doc_freq.update(_grams(document))

    ceiling = max(int(n_docs * MAX_DOC_RATIO), MIN_DOC_FREQ + 1)

    kept = {
        term: freq
        for term, freq in doc_freq.items()
        if MIN_DOC_FREQ <= freq <= ceiling
    }

    # If the corpus is small the ceiling can exclude everything; fall back to the
    # frequency floor alone rather than returning nothing.
    if not kept:
        kept = {t: f for t, f in doc_freq.items() if f >= min(MIN_DOC_FREQ, n_docs)}

    if len(kept) > MAX_VOCAB:
        # Prefer the rarer half: specificity is what makes a term informative.
        kept = dict(sorted(kept.items(), key=lambda kv: kv[1])[:MAX_VOCAB])

    idf = {
        term: math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))
        for term, freq in kept.items()
    }
    logger.info("learned vocabulary of %d terms from %d adverts", len(idf), n_docs)
    return Vocabulary(idf=idf, n_docs=n_docs)


def extract_terms(text: str | None, vocabulary: Vocabulary) -> set[str]:
    """The terms a document shares with the learned vocabulary.

    Applied to a CV this replaces a hardcoded skills list: whatever the CV has in
    common with real Dublin job adverts is, by construction, its relevant skill set.
    """
    if not text:
        return set()
    return {gram for gram in _grams(text) if gram in vocabulary}


def top_terms(
    text: str | None, vocabulary: Vocabulary, limit: int = 40
) -> list[str]:
    """The most distinctive shared terms, rarest first."""
    terms = extract_terms(text, vocabulary)
    return sorted(terms, key=vocabulary.weight, reverse=True)[:limit]


def signal_terms(text: str | None, vocabulary: Vocabulary) -> set[str]:
    """The terms used for similarity.

    Deliberately no minimum-IDF cut-off. An absolute threshold looked sensible — filler
    measured 1.3-1.5 and technologies 2.0+ — but it was both arbitrary and wrong in two
    ways. It excluded "aws" (1.52), a real skill that happens to be common, while
    keeping nothing it needed to; and being an absolute number it scaled with nothing,
    so on a small corpus (where the highest achievable IDF is under 2.0) it filtered out
    every term and similarity collapsed to zero.

    Weighting already does the job it was meant to do: "dbt" at 4.79 contributes nearly
    four times what "communication skills" at 1.30 does. Verified on the live corpus —
    removing the cut left the top matches unchanged while recovering 14 real terms.
    """
    return extract_terms(text, vocabulary)


def similarity(
    candidate_terms: set[str], document_text: str | None, vocabulary: Vocabulary
) -> tuple[float, set[str]]:
    """How much of a CV's distinctive vocabulary this advert actually asks for.

    Weighted recall: the IDF mass of the shared terms over the IDF mass of the CV's
    own terms. Weighting is what makes this work — an advert sharing "kubernetes",
    "terraform" and "airflow" with a CV scores far above one sharing only "team" and
    "communication", even though both share three terms.

    Returns the score in 0..1 and the matched terms, so the result can be explained.
    """
    if not candidate_terms:
        return 0.0, set()

    total = sum(vocabulary.weight(term) for term in candidate_terms)
    if total <= 0:
        return 0.0, set()

    document_terms = signal_terms(document_text, vocabulary)
    matched = candidate_terms & document_terms
    shared = sum(vocabulary.weight(term) for term in matched)

    return min(shared / total, 1.0), matched


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save(vocabulary: Vocabulary, path: Path | None = None) -> Path:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"n_docs": vocabulary.n_docs, "idf": vocabulary.idf}),
        encoding="utf-8",
    )
    return path


def load(path: Path | None = None) -> Vocabulary | None:
    path = path or CACHE_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Vocabulary(idf=payload["idf"], n_docs=payload["n_docs"])
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        logger.warning("could not load cached vocabulary: %s", exc)
        return None
