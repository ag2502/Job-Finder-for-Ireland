"""Job ranking.

Scores a job against a resume and a set of chosen fields, and — as importantly — says
*why* it scored that way. An opaque number is useless to a job seeker deciding where to
spend an afternoon; "matches 7 of your skills, right seniority, posted 2 days ago" is
something they can act on.

Four signals, blended:

1. **Skills**   - overlap between the resume's technologies and the posting's
2. **Field**    - does the title fall in a chosen field, or one hop away
3. **Text**     - BM25 over title and description against the resume's vocabulary
4. **Context**  - seniority fit and recency

BM25 is used rather than embeddings deliberately. Sentence-transformers pulls in ~2GB of
torch, which does not fit the free tiers this is designed to run on, and job matching is
dominated by exact technology and title tokens — precisely where lexical scoring is
strong and semantic similarity adds least. Embeddings remain a clean upgrade: the
interface here takes a job and returns a score, so swapping the text component changes
nothing else.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jobfinder.matching.corpus import Vocabulary
from jobfinder.matching.corpus import similarity as corpus_similarity
from jobfinder.normalize.taxonomy import (
    FIELDS,
    classify_title,
    expand_fields,
    extract_skills,
    skills_for,
)

# Weights sum to 1.0 before the recency multiplier is applied.
WEIGHT_SKILLS = 0.40
WEIGHT_FIELD = 0.30
WEIGHT_TEXT = 0.20
WEIGHT_SENIORITY = 0.10

BM25_K1 = 1.5
BM25_B = 0.75

_TOKEN = re.compile(r"[a-z0-9+#.]+")

_STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or that the to
with will you your we our us this these those they their he she them can may
role team work working experience opportunity job position candidate ability
""".split())


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [
        token
        for token in _TOKEN.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


@dataclass
class Candidate:
    """What the searcher is looking for.

    `corpus_terms` is the CV read against the vocabulary learned from the job adverts
    themselves - see matching/corpus.py. It is what makes relevance work for a CV with
    no fields ticked, without anyone maintaining a skills list.
    """

    skills: set[str] = field(default_factory=set)
    fields: list[str] = field(default_factory=list)
    seniority: str | None = None
    text: str = ""
    corpus_terms: set[str] = field(default_factory=set)

    @property
    def expanded_fields(self) -> list[str]:
        return expand_fields(self.fields)

    @property
    def query_tokens(self) -> list[str]:
        tokens = tokenize(self.text)
        # Skills and field terms are the highest-signal part of the query, so they are
        # repeated to weight them above incidental CV prose.
        for skill in self.skills:
            tokens.extend(tokenize(skill) * 3)
        # Terms learned from the corpus carry the same weight as declared skills: they
        # are drawn from the adverts themselves, so they are at least as trustworthy.
        for term in self.corpus_terms:
            tokens.extend(tokenize(term) * 2)
        for key in self.expanded_fields:
            for term in FIELDS[key].terms:
                tokens.extend(tokenize(term))
        return tokens


@dataclass
class ScoredJob:
    job_id: int
    score: float
    skill_matches: set[str] = field(default_factory=set)
    field_matches: list[str] = field(default_factory=list)
    seniority_fit: str = "unknown"
    reasons: list[str] = field(default_factory=list)
    corpus_matches: set[str] = field(default_factory=set)
    # False when the job is outside everything the searcher is qualified for or
    # interested in. Ranking alone is not enough: sorting an Art Director role to the
    # bottom still leaves it in a list the searcher has to read past.
    relevant: bool = True

    def explain(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "weak match"


# A job must share at least this many skills to count as relevant when its title does
# not place it in any field the searcher cares about. Tuned against the live Dublin
# set: at 2, thirteen off-target roles (product designers, compensation leads) slipped
# through on incidental keyword overlap; at 3 that falls to five while costing few
# genuine matches, since job adverts are long and name many technologies in passing.
MIN_SKILL_OVERLAP = 3

# For a CV with no fields ticked, relevance is judged against this search's own best
# match rather than an absolute score, because absolute scores shift with how specific
# the CV is. Tuned on the live corpus: lower keeps too much unrelated work, higher
# starts dropping genuine adjacent roles.
CORPUS_RELEVANCE_RATIO = 0.45

# Weighted recall tops out low - the best match on the live corpus scored 0.21, since
# no advert asks for an entire career - so it is rescaled to sit on the same 0..1 range
# as the other signals rather than flattening everything toward zero.
CORPUS_SCORE_GAIN = 4.0


class BM25Index:
    """Small in-memory BM25 index.

    Sized for a city's job market - tens of thousands of documents - where building the
    index costs milliseconds and avoids any external search dependency.
    """

    def __init__(self, documents: dict[int, str]) -> None:
        self.doc_tokens: dict[int, Counter[str]] = {}
        self.doc_len: dict[int, int] = {}
        self.df: Counter[str] = Counter()

        for doc_id, text in documents.items():
            tokens = tokenize(text)
            counts = Counter(tokens)
            self.doc_tokens[doc_id] = counts
            self.doc_len[doc_id] = len(tokens)
            self.df.update(counts.keys())

        self.n_docs = max(len(documents), 1)
        self.avg_len = (sum(self.doc_len.values()) / self.n_docs) if self.doc_len else 1.0
        self._idf_cache: dict[str, float] = {}

    def idf(self, term: str) -> float:
        if term not in self._idf_cache:
            df = self.df.get(term, 0)
            self._idf_cache[term] = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
        return self._idf_cache[term]

    def score(self, doc_id: int, query_tokens: list[str]) -> float:
        counts = self.doc_tokens.get(doc_id)
        if not counts:
            return 0.0

        length = self.doc_len.get(doc_id, 0) or 1
        norm = BM25_K1 * (1 - BM25_B + BM25_B * length / (self.avg_len or 1))

        total = 0.0
        for term in set(query_tokens):
            tf = counts.get(term, 0)
            if tf:
                total += self.idf(term) * (tf * (BM25_K1 + 1)) / (tf + norm)
        return total


def seniority_fit(candidate_level: str | None, title: str) -> tuple[float, str]:
    """Compare the searcher's level against the level implied by a job title."""
    if not candidate_level:
        return 0.5, "unknown"

    from jobfinder.matching.resume import SENIORITY_ORDER, detect_seniority

    job_level = detect_seniority(title)
    if not job_level:
        return 0.6, "unspecified"

    try:
        distance = abs(
            SENIORITY_ORDER.index(candidate_level) - SENIORITY_ORDER.index(job_level)
        )
    except ValueError:
        return 0.5, "unknown"

    if distance == 0:
        return 1.0, "exact"
    if distance == 1:
        return 0.7, "close"
    if distance == 2:
        return 0.35, "distant"
    return 0.1, "mismatch"


def recency_multiplier(posted_at: datetime | None) -> float:
    """Gently favour fresher postings without burying good older ones."""
    if not posted_at:
        return 0.95
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - posted_at).days
    if age_days <= 3:
        return 1.10
    if age_days <= 14:
        return 1.05
    if age_days <= 45:
        return 1.0
    if age_days <= 90:
        return 0.92
    return 0.85


def score_job(
    *,
    job_id: int,
    title: str,
    description: str | None,
    posted_at: datetime | None,
    candidate: Candidate,
    index: BM25Index,
    max_text_score: float,
    vocabulary: Vocabulary | None = None,
) -> ScoredJob:
    result = ScoredJob(job_id=job_id, score=0.0)
    haystack = f"{title}\n{description or ''}"

    # 1. Skills
    #
    # Two sources, and the corpus one is preferred where available. A hand-written
    # skills list only recognises what somebody thought to add; the corpus vocabulary
    # is derived from the adverts themselves, so it recognises whatever Dublin
    # employers are actually asking for this month.
    corpus_score = 0.0
    corpus_matches: set[str] = set()
    if candidate.corpus_terms and vocabulary is not None:
        corpus_score, corpus_matches = corpus_similarity(
            candidate.corpus_terms, haystack, vocabulary
        )

    job_skills = extract_skills(haystack)
    wanted = candidate.skills | skills_for(candidate.expanded_fields)
    matches = job_skills & candidate.skills
    result.skill_matches = matches
    result.corpus_matches = corpus_matches

    if job_skills:
        listed_score = len(matches) / min(len(job_skills), 12)
    elif wanted:
        listed_score = 0.0
    else:
        listed_score = 0.5

    if corpus_score > 0 or candidate.corpus_terms:
        # Weighted recall runs low in absolute terms even for excellent matches - the
        # best observed on the live corpus was 0.21 - because no single advert asks for
        # a whole career. Rescaling keeps it comparable with the other signals instead
        # of flattening every job to a near-zero score.
        skill_score = max(min(corpus_score * CORPUS_SCORE_GAIN, 1.0), listed_score)
    else:
        skill_score = listed_score

    skill_score = min(skill_score, 1.0)

    # 2. Field
    job_fields = classify_title(title)
    chosen = candidate.fields
    related = [f for f in candidate.expanded_fields if f not in chosen]

    direct = [f for f in job_fields if f in chosen]
    nearby = [f for f in job_fields if f in related]
    result.field_matches = direct + nearby

    if direct:
        field_score = 1.0
    elif nearby:
        field_score = 0.6
    elif not chosen:
        field_score = 0.5
    else:
        field_score = 0.0

    # 3. Text
    raw_text = index.score(job_id, candidate.query_tokens)
    text_score = min(raw_text / max_text_score, 1.0) if max_text_score > 0 else 0.0

    # 4. Seniority
    sen_score, sen_label = seniority_fit(candidate.seniority, title)
    result.seniority_fit = sen_label

    blended = (
        WEIGHT_SKILLS * skill_score
        + WEIGHT_FIELD * field_score
        + WEIGHT_TEXT * text_score
        + WEIGHT_SENIORITY * sen_score
    )
    result.score = round(min(blended * recency_multiplier(posted_at), 1.0) * 100, 1)

    # Relevance is a filter, not a ranking nudge. A searcher who uploads a backend CV
    # should not have to scroll past Art Director and Trust & Safety roles that merely
    # scored low.
    if not chosen and not candidate.skills:
        # Nothing to judge relevance against, so everything qualifies.
        result.relevant = True
    elif direct or nearby:
        result.relevant = True
    elif len(matches) >= MIN_SKILL_OVERLAP:
        # The title did not classify - plenty do not - but the advert demands enough of
        # this person's actual toolkit to be worth showing.
        result.relevant = True
    else:
        result.relevant = False

    if matches:
        shown = sorted(matches)[:6]
        result.reasons.append(
            f"matches {len(matches)} of your skills ({', '.join(shown)})"
        )
    if direct:
        result.reasons.append(
            f"in your chosen field: {FIELDS[direct[0]].label}"
        )
    elif nearby:
        result.reasons.append(f"related field: {FIELDS[nearby[0]].label}")
    if sen_label == "exact":
        result.reasons.append("seniority matches")
    elif sen_label == "mismatch":
        result.reasons.append("seniority looks off")
    if posted_at and (datetime.now(timezone.utc) - (
        posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
    )).days <= 3:
        result.reasons.append("posted in the last few days")

    return result


def rank_jobs(
    rows: list,
    candidate: Candidate,
    limit: int = 100,
    *,
    only_relevant: bool = False,
) -> list[ScoredJob]:
    """Score and sort jobs. `rows` are objects with id/title/description/posted_at.

    With `only_relevant`, jobs outside the searcher's fields and skills are dropped
    rather than merely ranked low.
    """
    if not rows:
        return []

    documents = {row.id: f"{row.title}\n{row.description or ''}" for row in rows}
    index = BM25Index(documents)

    # Loaded once per call rather than per job: it is a large dictionary.
    vocab = None
    if candidate.corpus_terms:
        try:
            from jobfinder.matching import vocabulary as _vocabulary

            vocab = _vocabulary.get()
        except Exception:  # noqa: BLE001 - ranking must survive a missing cache
            vocab = None

    query = candidate.query_tokens
    raw_scores = [index.score(row.id, query) for row in rows]
    max_text_score = max(raw_scores) if raw_scores else 0.0

    scored = [
        score_job(
            job_id=row.id,
            title=row.title,
            description=row.description,
            posted_at=row.posted_at,
            candidate=candidate,
            index=index,
            max_text_score=max_text_score,
            vocabulary=vocab,
        )
        for row in rows
    ]

    if only_relevant:
        # When the searcher ticked no fields, relevance cannot come from the taxonomy.
        # It comes from the corpus instead: keep whatever scores within a fraction of
        # the best match, so the cut-off is set by this search's own score distribution
        # rather than a fixed number that would be wrong for every other search.
        if not candidate.fields and (candidate.corpus_terms or candidate.skills):
            best = max((s.score for s in scored), default=0.0)
            if best > 0:
                cutoff = best * CORPUS_RELEVANCE_RATIO
                for entry in scored:
                    entry.relevant = entry.score >= cutoff

        filtered = [s for s in scored if s.relevant]
        # Never return an empty page when something was found: if the filter removes
        # everything, fall back to the best-scoring jobs so the searcher sees the
        # closest matches rather than a blank result they cannot act on.
        scored = filtered or sorted(scored, key=lambda s: s.score, reverse=True)[:25]

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]
