"""Resume parsing, taxonomy and ranking tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jobfinder.matching.rank import BM25Index, Candidate, rank_jobs, seniority_fit, tokenize
from jobfinder.matching.resume import detect_seniority, detect_years, parse_resume
from jobfinder.normalize.taxonomy import (
    classify_title,
    expand_fields,
    extract_skills,
)

CV = """
Amogh G
amogh.g2003@gmail.com

Senior Software Engineer at Acme Payments (2021-2026)
  Built event-driven microservices in Python and Go. Kafka, PostgreSQL, Redis.
  Deployed on Kubernetes with Terraform. 5 years of experience.

SKILLS
Python, Go, SQL, Kafka, PostgreSQL, Kubernetes, Terraform, AWS, React, C++, .NET, Node.js
"""


@dataclass
class FakeJob:
    id: int
    title: str
    description: str | None = None
    posted_at: datetime | None = None


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------


def test_related_fields_are_expanded_one_hop():
    expanded = expand_fields(["data-engineering"])
    assert "data-engineering" in expanded
    # The requirement: related fields come along automatically.
    assert "data-science" in expanded
    assert "business-intelligence" in expanded


def test_expansion_does_not_reach_the_whole_graph():
    """Two hops would make 'related' meaningless, so only one hop is taken."""
    expanded = expand_fields(["design"])
    assert "pharma" not in expanded
    assert "finance" not in expanded


def test_unknown_field_is_ignored():
    assert expand_fields(["not-a-real-field"]) == []


def test_classify_title():
    assert "software-engineering" in classify_title("Senior Software Engineer")
    assert "data-engineering" in classify_title("Data Engineer, Platform")
    assert "security-engineering" in classify_title("Application Security Engineer")
    assert "finance" in classify_title("Financial Analyst")


def test_skill_extraction_handles_punctuated_technology_names():
    """A plain \\b boundary silently misses C++, C#, .NET and Node.js."""
    found = extract_skills("Experienced in C++, C#, .NET and Node.js plus Python")
    assert {"c++", "c#", ".net", "node.js", "python"} <= found


def test_skill_extraction_avoids_substring_false_positives():
    # "Rust" should not be found inside "trust", nor "go" inside "algorithm".
    found = extract_skills("We value trust and good algorithms in a googly way")
    assert "rust" not in found
    assert "go" not in found


# --------------------------------------------------------------------------
# Resume parsing
# --------------------------------------------------------------------------


def test_parse_resume_extracts_signals():
    parsed = parse_resume(CV.encode(), "cv.txt")
    assert {"python", "go", "kafka", "kubernetes", "terraform"} <= parsed.skills
    assert parsed.seniority == "senior"
    assert parsed.years_experience == 5
    assert parsed.email == "amogh.g2003@gmail.com"
    assert "software-engineering" in parsed.fields


def test_parse_empty_resume_is_safe():
    parsed = parse_resume(b"", "empty.txt")
    assert parsed.skills == set()
    assert parsed.fields == []


def test_corrupt_pdf_does_not_raise():
    """A bad upload must degrade, not crash the request."""
    parsed = parse_resume(b"%PDF-1.4 this is not really a pdf", "broken.pdf")
    assert parsed.text == ""


def test_detect_seniority_prefers_the_most_senior_mention():
    assert detect_seniority("Director of Engineering") == "director"
    assert detect_seniority("Senior Software Engineer") == "senior"
    assert detect_seniority("Graduate Developer") == "junior"
    assert detect_seniority("Software Engineer") is None


def test_detect_years_from_date_span_when_unstated():
    assert detect_years("Worked 2018 - 2024 at various companies") == 6


def test_detect_years_ignores_implausible_values():
    assert detect_years("99 years experience") is None


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_relevant_job_outranks_irrelevant_one():
    candidate = Candidate(
        skills={"python", "kafka", "kubernetes"},
        fields=["backend"],
        seniority="senior",
        text="python kafka kubernetes backend distributed systems",
    )
    jobs = [
        FakeJob(1, "Senior Backend Engineer", "Python, Kafka and Kubernetes at scale"),
        FakeJob(2, "Warehouse Operative", "Pick and pack orders in our depot"),
    ]
    ranked = rank_jobs(jobs, candidate)
    assert ranked[0].job_id == 1
    assert ranked[0].score > ranked[1].score


def test_score_explains_itself():
    candidate = Candidate(skills={"python", "airflow"}, fields=["data-engineering"])
    jobs = [FakeJob(1, "Data Engineer", "Build pipelines with Python and Airflow")]
    top = rank_jobs(jobs, candidate)[0]

    assert top.skill_matches == {"python", "airflow"}
    assert "data-engineering" in top.field_matches
    assert "skills" in top.explain()


def test_related_field_scores_lower_than_chosen_field():
    candidate = Candidate(skills=set(), fields=["data-engineering"])
    jobs = [
        FakeJob(1, "Data Engineer", "pipelines"),
        FakeJob(2, "Data Scientist", "modelling"),  # related, not chosen
    ]
    ranked = {s.job_id: s for s in rank_jobs(jobs, candidate)}
    assert ranked[1].score > ranked[2].score


def test_recency_boost_favours_fresh_postings():
    candidate = Candidate(skills={"python"}, fields=["backend"])
    now = datetime.now(timezone.utc)
    jobs = [
        FakeJob(1, "Backend Engineer", "python", posted_at=now),
        FakeJob(2, "Backend Engineer", "python", posted_at=now - timedelta(days=200)),
    ]
    ranked = {s.job_id: s for s in rank_jobs(jobs, candidate)}
    assert ranked[1].score > ranked[2].score


def test_seniority_fit_scoring():
    assert seniority_fit("senior", "Senior Engineer")[1] == "exact"
    assert seniority_fit("senior", "Lead Engineer")[1] == "close"
    assert seniority_fit("intern", "Director of Engineering")[1] == "mismatch"
    assert seniority_fit(None, "Senior Engineer")[1] == "unknown"


def test_ranking_empty_input_is_safe():
    assert rank_jobs([], Candidate()) == []


def test_scores_are_bounded():
    candidate = Candidate(
        skills={"python", "go", "kafka", "aws", "kubernetes"},
        fields=["backend"],
        seniority="senior",
        text="python go kafka aws kubernetes " * 50,
    )
    jobs = [FakeJob(1, "Senior Backend Engineer", "python go kafka aws kubernetes")]
    score = rank_jobs(jobs, candidate)[0].score
    assert 0 <= score <= 100


def test_bm25_ranks_by_term_overlap():
    index = BM25Index({1: "python kafka backend", 2: "marketing brand campaign"})
    query = tokenize("python kafka")
    assert index.score(1, query) > index.score(2, query)


def test_tokenize_drops_stopwords():
    assert "the" not in tokenize("The engineer works with the team")
    assert "engineer" in tokenize("The engineer works with the team")


def test_skill_prefilter_never_changes_what_is_found():
    """The substring pre-check only skips regexes that could not have matched.

    It exists for speed - every search runs skill extraction over every candidate job -
    so it must be invisible in the results, including for case changes and punctuated
    names where a naive substring test and a boundary-aware regex disagree.
    """
    from jobfinder.normalize.taxonomy import _SKILL_PATTERNS

    samples = [
        "Senior PYTHON developer, Node.JS and C# on .NET Core; some c++ and Go-lang",
        "We value trust and good algorithms in a googly way",
        "Kubernetes/Terraform on AWS. SQL, NoSQL, PostgreSQL. A/B Testing and Agile.",
        "ReactJS, React Native, TypeScript; javascript not Java. R and Rust.",
        "",
        "accessibility, ACCA and AML compliance; Adobe XD prototypes",
    ]
    for text in samples:
        brute_force = {s for s, (_, pattern) in _SKILL_PATTERNS.items() if pattern.search(text)}
        assert extract_skills(text) == brute_force, text
