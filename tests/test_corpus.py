"""Corpus-derived vocabulary.

The point of this module is that nothing is hardcoded: the skill vocabulary is learned
from the job adverts themselves, so it tracks whatever employers are actually asking
for rather than a list somebody has to maintain.
"""

from __future__ import annotations

from jobfinder.matching import corpus

# A miniature corpus with a clear split: engineering adverts, a finance advert, and
# boilerplate repeated across all of them.
BOILERPLATE = (
    " We are an equal opportunity employer. Excellent communication skills required. "
    "Salary range competitive. See our privacy notice."
)

ENGINEERING = [
    "Backend Engineer. Build services in Python with Kubernetes and Terraform." + BOILERPLATE,
    "Platform Engineer. Python, Kubernetes, Terraform and Kafka at scale." + BOILERPLATE,
    "Site Reliability Engineer. Kubernetes, Terraform, Prometheus monitoring." + BOILERPLATE,
    "Data Engineer. Build ETL pipelines with Airflow, dbt and Python." + BOILERPLATE,
    "Senior Data Engineer. Airflow, dbt, Snowflake and Python pipelines." + BOILERPLATE,
]

FINANCE = [
    "Financial Accountant. Prepare statutory accounts under IFRS." + BOILERPLATE,
    "Finance Manager. Budgeting, forecasting and audit under IFRS." + BOILERPLATE,
    "Financial Controller. IFRS reporting, audit and statutory accounts." + BOILERPLATE,
]

DOCUMENTS = ENGINEERING + FINANCE

ENGINEERING_CV = """
Backend engineer. Built services in Python with Kubernetes and Terraform.
ETL pipelines using Airflow and dbt.
"""


def _vocab(**overrides):
    """Build with thresholds suited to a tiny test corpus."""
    original = (corpus.MIN_DOC_FREQ, corpus.MAX_DOC_RATIO)
    corpus.MIN_DOC_FREQ = overrides.get("min_doc_freq", 2)
    corpus.MAX_DOC_RATIO = overrides.get("max_doc_ratio", 0.9)
    try:
        return corpus.build(DOCUMENTS)
    finally:
        corpus.MIN_DOC_FREQ, corpus.MAX_DOC_RATIO = original


def test_vocabulary_is_learned_from_the_documents():
    vocab = _vocab()
    assert vocab.n_docs == len(DOCUMENTS)
    assert vocab.size > 0
    # Terms nobody hardcoded, discovered purely from the adverts.
    assert "kubernetes" in vocab
    assert "terraform" in vocab
    assert "ifrs" in vocab


def test_idf_downweights_boilerplate_without_a_blocklist():
    """This is what replaces a stopword list for domain filler: boilerplate appears in
    every advert, so its IDF collapses while real skills keep theirs."""
    vocab = _vocab()
    filler = vocab.weight("communication skills")
    signal = vocab.weight("kubernetes")
    assert signal > filler, f"kubernetes {signal} should outweigh filler {filler}"


def test_bigrams_are_captured():
    vocab = _vocab()
    assert any(" " in term for term in vocab.idf), "expected multi-word terms"


def test_bigrams_do_not_span_sentence_boundaries():
    """"...at scale. Skills include..." must not yield the term "scale skills"."""
    grams = corpus._grams("deployed at scale. Skills include python")
    assert "scale skills" not in grams
    assert "skills python" in grams  # "include" is a stopword, so it drops out


def test_trailing_dots_are_trimmed_but_internal_ones_survive():
    tokens = corpus.tokenize("Python go. We use node.js and .NET daily")
    assert "python" in tokens
    assert "go" in tokens          # not "go."
    assert "node.js" in tokens     # internal dot preserved
    assert "go." not in tokens


def test_similarity_ranks_the_right_domain_higher():
    vocab = _vocab()
    cv_terms = corpus.signal_terms(ENGINEERING_CV, vocab)
    assert cv_terms, "expected the CV to share vocabulary with the corpus"

    eng_score, eng_matched = corpus.similarity(cv_terms, ENGINEERING[0], vocab)
    fin_score, _ = corpus.similarity(cv_terms, FINANCE[0], vocab)

    assert eng_score > fin_score
    assert eng_matched, "matched terms should be reported so the score can be explained"


def test_similarity_of_unrelated_text_is_lower_than_a_match():
    vocab = _vocab()
    cv_terms = corpus.signal_terms(ENGINEERING_CV, vocab)
    unrelated, _ = corpus.similarity(cv_terms, FINANCE[2], vocab)
    related, _ = corpus.similarity(cv_terms, ENGINEERING[0], vocab)
    assert unrelated < related


def test_similarity_works_on_a_small_corpus():
    """Regression: an absolute minimum-IDF cut-off filtered out every term on a corpus
    too small to reach it, collapsing all similarity to zero."""
    vocab = _vocab()
    cv_terms = corpus.signal_terms(ENGINEERING_CV, vocab)
    assert cv_terms, "a small corpus must still yield usable terms"
    score, matched = corpus.similarity(cv_terms, ENGINEERING[0], vocab)
    assert score > 0 and matched


def test_similarity_with_no_candidate_terms_is_zero():
    vocab = _vocab()
    assert corpus.similarity(set(), ENGINEERING[0], vocab) == (0.0, set())


def test_empty_corpus_is_safe():
    vocab = corpus.build([])
    assert vocab.size == 0
    assert corpus.extract_terms("anything", vocab) == set()
    assert corpus.similarity({"python"}, "python", vocab) == (0.0, set())


def test_roundtrip_through_disk(tmp_path):
    vocab = _vocab()
    path = corpus.save(vocab, tmp_path / "vocab.json")
    loaded = corpus.load(path)

    assert loaded is not None
    assert loaded.n_docs == vocab.n_docs
    assert loaded.size == vocab.size
    assert loaded.weight("kubernetes") == vocab.weight("kubernetes")


def test_load_missing_file_returns_none(tmp_path):
    assert corpus.load(tmp_path / "absent.json") is None


def test_load_corrupt_file_returns_none_rather_than_raising(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert corpus.load(path) is None
