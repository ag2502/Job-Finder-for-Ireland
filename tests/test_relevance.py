"""Relevance filtering.

Ranking alone is not enough. Sorting an Art Director role to the bottom still leaves it
in a list the searcher has to read past, so anything outside their fields and skills is
removed rather than merely demoted.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobfinder.matching.rank import Candidate, rank_jobs
from jobfinder.normalize.taxonomy import classify_title


@dataclass
class FakeJob:
    id: int
    title: str
    description: str | None = None
    posted_at: None = None


BACKEND = Candidate(
    skills={"python", "go", "kafka", "kubernetes", "terraform", "aws", "sql"},
    fields=["software-engineering", "data-engineering"],
    seniority="senior",
    text="python go kafka kubernetes terraform aws distributed systems",
)


def _titles(scored, jobs):
    by_id = {j.id: j for j in jobs}
    return [by_id[s.job_id].title for s in scored]


def test_irrelevant_roles_are_removed_not_just_demoted():
    jobs = [
        FakeJob(1, "Senior Backend Engineer", "Python, Kafka, Kubernetes"),
        FakeJob(2, "Art Director", "Lead our brand and visual identity"),
        FakeJob(3, "Water Systems Design Engineer", "Design cooling water systems"),
        FakeJob(4, "Technician, re:Cycle Reverse Logistics", "Warehouse hardware"),
    ]
    kept = _titles(rank_jobs(jobs, BACKEND, only_relevant=True), jobs)

    assert "Senior Backend Engineer" in kept
    assert "Art Director" not in kept
    assert "Water Systems Design Engineer" not in kept


def test_amazon_style_titles_classify():
    """Regression: Amazon titles thousands of roles "Software Dev Engineer" / "SDE",
    none containing the exact string "software engineer"."""
    for title in (
        "Software Dev Engineer, CloudFront",
        "SDE II, AWS DMS",
        "Software Development Engineer, Penrose Networks",
    ):
        assert "software-engineering" in classify_title(title), title


def test_relevant_role_with_unclassifiable_title_survives_on_skills():
    jobs = [
        FakeJob(
            1,
            "Platform Specialist",  # not a recognised title pattern
            "You will work with Python, Kubernetes, Terraform and Kafka daily",
        ),
        FakeJob(2, "Brand Manager", "Own our brand voice and campaigns"),
    ]
    kept = _titles(rank_jobs(jobs, BACKEND, only_relevant=True), jobs)
    assert "Platform Specialist" in kept
    assert "Brand Manager" not in kept


def test_incidental_keyword_overlap_is_not_enough():
    """A designer advert mentioning SQL once must not qualify as a backend match."""
    jobs = [
        FakeJob(1, "Senior Backend Engineer", "Python Kafka Kubernetes"),
        FakeJob(2, "Staff Product Designer", "Some familiarity with SQL is a plus"),
    ]
    kept = _titles(rank_jobs(jobs, BACKEND, only_relevant=True), jobs)
    assert "Staff Product Designer" not in kept


def test_related_fields_are_kept():
    """Expansion is the point: a DevOps role is relevant to a backend engineer."""
    jobs = [
        FakeJob(1, "Site Reliability Engineer", "Kubernetes and Terraform"),
        FakeJob(2, "Financial Controller", "IFRS reporting and audit"),
    ]
    kept = _titles(rank_jobs(jobs, BACKEND, only_relevant=True), jobs)
    assert "Site Reliability Engineer" in kept
    assert "Financial Controller" not in kept


def test_no_criteria_means_everything_is_relevant():
    """With neither fields nor skills there is nothing to judge against."""
    jobs = [FakeJob(1, "Art Director"), FakeJob(2, "Backend Engineer")]
    kept = _titles(rank_jobs(jobs, Candidate(), only_relevant=True), jobs)
    assert len(kept) == 2


def test_filter_never_returns_an_empty_page_when_jobs_exist():
    """A blank result the searcher cannot act on is worse than close-but-imperfect
    matches, so the filter falls back to the best scoring jobs."""
    jobs = [
        FakeJob(1, "Art Director", "Brand and visual identity"),
        FakeJob(2, "Financial Controller", "IFRS and audit"),
    ]
    kept = rank_jobs(jobs, BACKEND, only_relevant=True)
    assert len(kept) > 0


def test_only_relevant_is_opt_in():
    jobs = [
        FakeJob(1, "Senior Backend Engineer", "Python"),
        FakeJob(2, "Art Director", "Brand"),
    ]
    assert len(rank_jobs(jobs, BACKEND)) == 2
    assert len(rank_jobs(jobs, BACKEND, only_relevant=True)) == 1


def test_ordering_is_still_by_score():
    jobs = [
        FakeJob(1, "Backend Engineer", "python"),
        FakeJob(2, "Senior Backend Engineer", "python kafka kubernetes terraform aws"),
    ]
    scored = rank_jobs(jobs, BACKEND, only_relevant=True)
    assert scored[0].score >= scored[1].score
