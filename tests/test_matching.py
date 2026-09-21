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


def test_a_term_must_start_at_a_word_boundary():
    """A raw substring test read "ux" out of "(Benelux)" and "sales" out of "Presales",
    filing those roles under fields they have nothing to do with."""
    assert "design" not in classify_title("Account Executive, SMB (Benelux)")
    assert "sales" not in classify_title("Solutions Architect, Platforms (Presales)")
    assert "mobile" not in classify_title("Senior Engineer, Studios")
    assert "cloud" not in classify_title("Legal Counsel - Employment Laws")


def test_terms_still_match_their_inflections():
    """The boundary is deliberately one-sided: the right-hand end stays open so a term
    keeps matching the longer word it heads."""
    assert "software-engineering" in classify_title("Software Development Engineer")
    assert "design" in classify_title("Senior Product Designer")
    assert "marketing" in classify_title("Integrated Campaigns Manager")


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
    assert seniority_fit("senior", "Lead Engineer")[1] == "stretch"
    assert seniority_fit("intern", "Director of Engineering")[1] == "mismatch"
    assert seniority_fit(None, "Senior Engineer")[1] == "unknown"


def test_seniority_fit_is_signed_not_a_distance():
    """Being under-levelled and over-levelled are not the same problem.

    A junior cannot get a Staff role; a Staff engineer can take a mid-level one and may
    well want to. An absolute distance scored both alike.
    """
    over = seniority_fit("junior", "Principal Engineer")
    under = seniority_fit("principal", "Junior Engineer")
    assert over[1] == "mismatch"
    assert under[1] == "below"
    assert under[0] > over[0]


def test_mid_is_reachable_so_distances_are_not_inflated():
    """`mid` sat in SENIORITY_ORDER with no pattern to produce it, so it padded every
    gap that spanned it: junior against senior read as two levels apart."""
    assert detect_seniority("Software Engineer II") == "mid"
    assert detect_seniority("Mid-Level Developer") == "mid"
    # "Senior" still wins where both could match.
    assert detect_seniority("Senior Engineer II") == "senior"
    # One rung apart is now genuinely one rung, rather than two with a hole in between.
    assert seniority_fit("mid", "Senior Engineer")[1] == "stretch"


def test_typed_years_outrank_a_cv_that_says_otherwise():
    """`detect_seniority` reads CV prose first-match-wins, so a line like "I lead the
    migration" makes a graduate `lead`. An explicit figure is a statement of fact."""
    assert Candidate(seniority="lead", years=1).level == "junior"
    # With nothing typed the CV is still all there is to go on.
    assert Candidate(seniority="lead", years=None).level == "lead"


def test_stated_years_hide_roles_well_above_the_searcher():
    """The reported bug: one year of experience, and Staff roles ranked first.

    These titles carry no stated minimum, so the eligibility filter passes them through
    on the advert alone; the level implied by the title is the only thing left to catch
    them.
    """
    jobs = [
        FakeJob(1, "Backend Engineer", "python"),
        FakeJob(2, "Staff Backend Engineer", "python"),
        FakeJob(3, "Principal Backend Engineer", "python"),
        FakeJob(4, "Director of Backend Engineering", "python"),
    ]
    kept = {
        s.job_id
        for s in rank_jobs(jobs, Candidate(fields=["backend"], years=1), only_relevant=True)
    }
    assert kept == {1}


def test_one_level_up_is_kept_as_a_stretch_and_demoted():
    """Near-miss seniority is offered rather than hidden - it is reachable - but it
    never outranks work at the searcher's own level."""
    jobs = [
        FakeJob(1, "Backend Engineer", "python"),
        FakeJob(2, "Senior Backend Engineer", "python"),
    ]
    scored = rank_jobs(jobs, Candidate(fields=["backend"], years=4), only_relevant=True)
    assert {s.job_id for s in scored} == {1, 2}
    assert [s.job_id for s in scored] == [1, 2], "the stretch role ranks below"
    assert next(s for s in scored if s.job_id == 2).seniority_fit == "stretch"


def test_an_inferred_level_never_hides_a_job():
    """Only a figure the searcher typed may remove work. A blank box must still show
    everything, even when the CV implies a level."""
    jobs = [
        FakeJob(1, "Backend Engineer", "python"),
        FakeJob(2, "Principal Backend Engineer", "python"),
    ]
    candidate = Candidate(fields=["backend"], seniority="junior", years=None)
    kept = {s.job_id for s in rank_jobs(jobs, candidate, only_relevant=True)}
    assert kept == {1, 2}


def test_chosen_fields_always_outrank_related_ones():
    """The reported bug: picking Backend put ".Net Developer" and "Front End Developer"
    above real backend roles, because a 12-point field gap lost to a 20-point text term.
    """
    jobs = [
        # Nothing in the description to score on - only the title places it.
        FakeJob(1, "Backend Engineer", ""),
        # A related field (software-engineering) with everything going for it.
        FakeJob(2, "Front End Developer", "python go kafka aws kubernetes " * 20),
    ]
    order = [s.job_id for s in rank_jobs(jobs, Candidate(fields=["backend"]))]
    assert order == [1, 2]


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
