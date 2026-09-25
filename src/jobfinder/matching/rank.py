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

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property, lru_cache

from jobfinder.matching.corpus import Vocabulary
from jobfinder.matching.corpus import signal_terms
from jobfinder.normalize.taxonomy import (
    FIELDS,
    NEAR,
    classify_title,
    expand_fields,
    extract_skills,
    near_fields,
    relatedness,
    skills_for,
)

# Weights sum to 1.0 before the recency multiplier is applied.
WEIGHT_SKILLS = 0.40
WEIGHT_FIELD = 0.30
WEIGHT_TEXT = 0.20
WEIGHT_SENIORITY = 0.10

# The bands a result falls in, compared before the score. See ScoredJob.tier.
TIER_CHOSEN = 0
TIER_CV = 1
TIER_NEAR = 2
TIER_FAR = 3
TIER_SKILLS = 4

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


# ------------------------------------------------------------ per-advert cache
#
# Everything about an advert that does not depend on who is searching: its term counts,
# the skills it names, the fields its title falls in, the level its title implies. These
# were recomputed for every job on every search - 188 skill regexes over ~1,500 full
# descriptions - which was 95% of a search's time and made one take 4s on a laptop and
# 10s on Vercel. Worked out once per process instead, a search only does the part that
# depends on the searcher.
#
# Keyed by the text itself rather than a job id, so a row whose advert changes under a
# long-running local server can never be scored on its old wording.


@dataclass(slots=True)
class _Advert:
    counts: Counter[str]
    length: int
    skills: frozenset[str]
    # The corpus reading, filled on first use: only a search with a CV needs it, and it
    # belongs to one vocabulary, which is rebuilt by the crawler.
    signal: frozenset[str] | None = None
    signal_vocab: int = 0


_ADVERTS: dict[str, _Advert] = {}
_ADVERTS_MAX = 25_000

# Skills worked out ahead of time by `scripts/export_snapshot.py`, keyed by
# `advert_hash`. A fresh Vercel instance starts with the cache above empty, and
# filling it cost the first search after every cold start the whole 10 seconds again;
# with these, the only per-advert work left on a cold start is tokenising.
_PRECOMPUTED_SKILLS: dict[str, frozenset[str]] = {}


def advert_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def skills_fingerprint() -> str:
    """Identifies the skill patterns, so precomputed skills from a snapshot built by
    different code are ignored rather than trusted."""
    from jobfinder.normalize import taxonomy

    source = "\n".join(sorted(p.pattern for _, p in taxonomy._SKILL_PATTERNS.values()))
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def preload_skills(fingerprint: str, rows) -> int:
    """Accept `(advert_hash, skills)` pairs computed ahead of time.

    Returns how many were taken - none when they came from a different skills list.
    """
    if fingerprint != skills_fingerprint():
        return 0
    for key, skills in rows:
        _PRECOMPUTED_SKILLS[key] = frozenset(skills)
    return len(_PRECOMPUTED_SKILLS)


def _advert(text: str) -> _Advert:
    found = _ADVERTS.get(text)
    if found is None:
        if len(_ADVERTS) >= _ADVERTS_MAX:
            _ADVERTS.clear()
        skills = (
            _PRECOMPUTED_SKILLS.get(advert_hash(text)) if _PRECOMPUTED_SKILLS else None
        )
        tokens = tokenize(text)
        found = _ADVERTS[text] = _Advert(
            counts=Counter(tokens),
            length=len(tokens),
            skills=skills if skills is not None else frozenset(extract_skills(text)),
        )
    return found


def _advert_signal(text: str, vocabulary: Vocabulary) -> frozenset[str]:
    advert = _advert(text)
    if advert.signal is None or advert.signal_vocab != id(vocabulary):
        advert.signal = frozenset(signal_terms(text, vocabulary))
        advert.signal_vocab = id(vocabulary)
    return advert.signal


@lru_cache(maxsize=50_000)
def _title_fields(title: str) -> tuple[str, ...]:
    return tuple(classify_title(title))


@lru_cache(maxsize=50_000)
def _title_level(title: str) -> str | None:
    from jobfinder.matching.resume import detect_seniority

    return detect_seniority(title)


def corpus_similarity(
    candidate_terms: set[str], document_text: str | None, vocabulary: Vocabulary
) -> tuple[float, set[str]]:
    """`corpus.similarity`, reading the advert's terms from the cache.

    Kept arithmetically identical to the original so ranking does not move: the weighted
    recall of the CV's terms that this advert shares.
    """
    if not candidate_terms:
        return 0.0, set()
    total = sum(vocabulary.weight(term) for term in candidate_terms)
    if total <= 0:
        return 0.0, set()
    matched = candidate_terms & _advert_signal(document_text or "", vocabulary)
    shared = sum(vocabulary.weight(term) for term in matched)
    return min(shared / total, 1.0), set(matched)


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
    # What the searcher typed in the years box. None means they stated nothing.
    years: int | None = None
    # The fields their CV points to, as read from it. Offered alongside the chosen
    # fields in a band of their own, never merged into them: the boxes say what someone
    # wants next, the CV only what they have done.
    cv_fields: list[str] = field(default_factory=list)

    @cached_property
    def expanded_fields(self) -> list[str]:
        """Everything one hop out, however loose the hop. Used to judge relevance."""
        return expand_fields(self.fields)

    @cached_property
    def related_fields(self) -> dict[str, float]:
        """Neighbouring fields mapped to how close each is - see taxonomy.relatedness."""
        return relatedness(self.fields)

    @cached_property
    def near_fields(self) -> list[str]:
        """Chosen fields plus the neighbours close enough to search as if asked for."""
        return near_fields(self.fields)

    @cached_property
    def level(self) -> str | None:
        """The searcher's seniority: what they typed wins over what their CV implies.

        `detect_seniority` reads the whole CV body and the first pattern to hit wins, so a
        line like "I lead the migration project" resolves a graduate to `lead`. A number
        in the box is a statement of fact about the searcher; the CV is an inference from
        prose, so the explicit figure takes precedence wherever there is one.
        """
        from jobfinder.matching.resume import seniority_from_years

        return seniority_from_years(self.years) or self.seniority

    @cached_property
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
        for key in self.near_fields:
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
    # TIER_CHOSEN = in a field the searcher ticked, TIER_CV = in a field their CV points
    # to, TIER_NEAR = a near neighbour, TIER_FAR = a far one (a real pivot, not the same
    # job), TIER_SKILLS = kept on skill overlap alone. Compared *before* the
    # score, so a related role can never outrank a chosen one on text-score noise. A
    # 0.4 gap in the field signal is worth 12 points, which the 20-point text term
    # overwhelmed: picking "Backend" put ".Net Developer" and "Front End Developer"
    # above real backend roles. Splitting 1 from 2 is the same argument one level down:
    # Software Engineering is the largest bucket in the corpus and neighbours half the
    # taxonomy, so without the split a Machine Learning search filled with graduate
    # developer roles before it reached a single Data Science one.
    tier: int = TIER_CHOSEN
    # True when this job survived only because the relevance filter emptied the list.
    fallback: bool = False

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
            advert = _advert(text)
            self.doc_tokens[doc_id] = advert.counts
            self.doc_len[doc_id] = advert.length
            self.df.update(advert.counts.keys())

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
    """Compare the searcher's level against the level implied by a job title.

    The comparison is **signed**, not a distance. A one-year searcher looking at a Staff
    role and a Staff engineer looking at a one-year role are not the same situation: the
    first cannot get the job, the second is merely overqualified and may still want it.
    So roles at or below the searcher's level stay near the top, one level up is a
    reachable stretch, and anything further up is a mismatch the caller drops outright.
    """
    if not candidate_level:
        return 0.5, "unknown"

    from jobfinder.matching.resume import SENIORITY_ORDER

    job_level = _title_level(title)
    if not job_level:
        return 0.6, "unspecified"

    try:
        gap = SENIORITY_ORDER.index(job_level) - SENIORITY_ORDER.index(candidate_level)
    except ValueError:
        return 0.5, "unknown"

    if gap == 0:
        return 1.0, "exact"
    if gap < 0:
        return 0.75, "below"
    if gap == 1:
        return 0.55, "stretch"
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

    job_skills = _advert(haystack).skills
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
    job_fields = _title_fields(title)
    chosen = candidate.fields
    related = candidate.related_fields

    direct = [f for f in job_fields if f in chosen]
    from_cv = [f for f in job_fields if f in candidate.cv_fields and f not in chosen]
    # Closest neighbour first, so a job that classifies as both Data Science and
    # Software Engineering is judged - and explained - as the Data Science role.
    nearby = sorted((f for f in job_fields if f in related), key=lambda f: -related[f])
    result.field_matches = direct + from_cv + nearby

    closeness = related[nearby[0]] if nearby else 0.0

    if direct:
        field_score = 1.0
        result.tier = TIER_CHOSEN
    elif from_cv:
        # Below a chosen field, above any neighbour of one: the CV is evidence about
        # this person, where a neighbouring field is only a guess about the taxonomy.
        field_score = 0.8
        result.tier = TIER_CV
    elif nearby:
        field_score = 0.6 * closeness
        result.tier = TIER_NEAR if closeness >= NEAR else TIER_FAR
    elif not chosen:
        field_score = 0.5
        result.tier = TIER_CHOSEN
    else:
        field_score = 0.0
        result.tier = TIER_SKILLS

    # 3. Text
    raw_text = index.score(job_id, candidate.query_tokens)
    text_score = min(raw_text / max_text_score, 1.0) if max_text_score > 0 else 0.0

    # 4. Seniority
    sen_score, sen_label = seniority_fit(candidate.level, title)
    result.seniority_fit = sen_label

    # The weights are renormalised over the signals this search actually has. Without a
    # CV there is no skills signal at all, and leaving its weight in the blend spent 40%
    # of every score on a constant zero - capping the best possible match at 55 and
    # handing the ranking to the 20% text term, which is largely noise.
    contributions = [
        (WEIGHT_FIELD, field_score),
        (WEIGHT_TEXT, text_score),
        (WEIGHT_SENIORITY, sen_score),
    ]
    if candidate.skills or candidate.corpus_terms:
        contributions.append((WEIGHT_SKILLS, skill_score))

    total_weight = sum(weight for weight, _ in contributions)
    blended = sum(weight * value for weight, value in contributions) / total_weight
    result.score = round(min(blended * recency_multiplier(posted_at), 1.0) * 100, 1)

    # Relevance is a filter, not a ranking nudge. A searcher who uploads a backend CV
    # should not have to scroll past Art Director and Trust & Safety roles that merely
    # scored low.
    if not chosen and not candidate.skills:
        # Nothing to judge relevance against, so everything qualifies.
        result.relevant = True
    elif direct or from_cv or nearby:
        result.relevant = True
    elif len(matches) >= MIN_SKILL_OVERLAP:
        # The title did not classify - plenty do not - but the advert demands enough of
        # this person's actual toolkit to be worth showing.
        result.relevant = True
    else:
        result.relevant = False

    # A role two or more levels above the searcher is not a stretch, it is a different
    # job. Demoting it still leaves it in a list they have to read past, which is how a
    # searcher with one year of experience was being shown Staff and Principal roles.
    # One level up stays - that is a reachable stretch, and 0.55 already ranks it below
    # the roles at their own level.
    #
    # Only a figure the searcher actually typed may remove a job. A level inferred from
    # CV prose is far too unreliable to hide work on: `detect_seniority` reads the whole
    # document and takes the first hit, so one sentence containing "lead" would silently
    # cut every Principal role. It still moves the score, as it always has.
    if result.relevant and sen_label == "mismatch" and candidate.years is not None:
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
    elif from_cv:
        result.reasons.append(f"where your CV points: {FIELDS[from_cv[0]].label}")
    elif nearby and closeness >= NEAR:
        result.reasons.append(f"related field: {FIELDS[nearby[0]].label}")
    elif nearby:
        # Honest about what this is. Calling a graduate developer job a "related field"
        # to Machine Learning is how the list stopped meaning anything.
        result.reasons.append(f"a sideways move into {FIELDS[nearby[0]].label}")
    if sen_label == "exact":
        result.reasons.append("seniority matches")
    elif sen_label == "stretch":
        result.reasons.append("a level above your experience")
    elif sen_label == "below":
        result.reasons.append("below your experience level")
    elif sen_label == "mismatch":
        # Only reachable when the searcher stated no years; with a figure it is filtered.
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
        # closest matches rather than a blank result they cannot act on. They are
        # flagged, because presenting them as ordinary results claims a match that the
        # filter just decided was not there.
        if not filtered:
            filtered = sorted(scored, key=lambda s: s.score, reverse=True)[:25]
            for entry in filtered:
                entry.fallback = True
        scored = filtered

    # Tier before score: see ScoredJob.tier.
    scored.sort(key=lambda s: (s.tier, -s.score))
    return scored[:limit]
