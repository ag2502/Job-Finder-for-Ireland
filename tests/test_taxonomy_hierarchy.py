"""The two-level taxonomy, weighted relatedness, and reading a CV with a model.

The bug these exist to stop recurring: picking Machine Learning returned a page of
"Software Developer Graduate". Two separate causes, one per half of this file - an
unweighted edge into the largest bucket in the corpus, and a CV reader that could only
see keywords it already knew.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from jobfinder.matching import llm_profile
from jobfinder.matching.rank import Candidate, rank_jobs
from jobfinder.normalize.taxonomy import (
    FAR,
    FIELDS,
    GROUP_LABELS,
    GROUPS,
    NEAR,
    classify_title,
    expand_fields,
    near_fields,
    relatedness,
)


@dataclass
class FakeJob:
    id: int
    title: str
    description: str | None = None
    posted_at: datetime | None = None


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_every_field_belongs_to_a_declared_group():
    for key, field_obj in FIELDS.items():
        assert field_obj.group in GROUP_LABELS, f"{key} has no group"


def test_every_group_holds_at_least_one_field():
    for group, label in GROUPS:
        assert any(f.group == group for f in FIELDS.values()), f"{label} is empty"


def test_relatedness_never_points_at_a_field_that_does_not_exist():
    """A dangling edge is silent: it scores nothing and classifies nothing."""
    for key, field_obj in FIELDS.items():
        for neighbour, _closeness in field_obj.related:
            assert neighbour in FIELDS, f"{key} -> {neighbour}"


def test_a_field_is_never_its_own_neighbour():
    for key, field_obj in FIELDS.items():
        assert key not in [n for n, _ in field_obj.related]


# --------------------------------------------------------------------------
# Weighted relatedness - the reported bug
# --------------------------------------------------------------------------


def test_machine_learning_holds_software_engineering_at_arms_length():
    """The reported bug. Data Science is next door; a graduate developer job is not."""
    near = relatedness(["machine-learning"])
    assert near["data-science"] == NEAR
    assert near["data-engineering"] == NEAR
    assert near["software-engineering"] == FAR


def test_a_far_neighbour_is_still_offered():
    """Demoted, not hidden. An ML engineer can take a general software role."""
    assert "software-engineering" in expand_fields(["machine-learning"])


def test_a_far_neighbours_vocabulary_stays_out_of_the_query():
    """The second amplifier: searching every Software Engineering term as well pulled
    the whole ranking towards the largest bucket a second time."""
    near = near_fields(["machine-learning"])
    assert "data-science" in near
    assert "software-engineering" not in near


def test_chosen_fields_are_not_listed_as_related_to_themselves():
    assert "backend" not in relatedness(["backend", "machine-learning"])


def test_ticking_the_neighbour_too_promotes_it():
    """Someone who asked for both has asked for enough of the software side that a
    general software role is no longer a sideways move."""
    alone = relatedness(["machine-learning"])
    both = relatedness(["machine-learning", "frontend"])
    assert alone["software-engineering"] == FAR
    assert both["software-engineering"] == NEAR


def test_data_science_roles_outrank_graduate_developer_roles_for_an_ml_search():
    jobs = [
        FakeJob(1, "Software Developer Graduate: September 2027 Dublin"),
        FakeJob(2, "Software Engineer, New Grad"),
        FakeJob(3, "Data Scientist, Payments"),
        FakeJob(4, "Machine Learning Engineer"),
    ]
    ranked = rank_jobs(jobs, Candidate(fields=["machine-learning"]), limit=10)
    order = [entry.job_id for entry in ranked]
    assert order[0] == 4, "the chosen field must come first"
    assert order.index(3) < order.index(1), "Data Science must beat a graduate dev role"
    assert order.index(3) < order.index(2)


def test_tiers_separate_the_chosen_the_near_and_the_far():
    jobs = [
        FakeJob(1, "Machine Learning Engineer"),
        FakeJob(2, "Data Scientist"),
        FakeJob(3, "Backend Software Engineer"),
    ]
    tiers = {
        entry.job_id: entry.tier
        for entry in rank_jobs(jobs, Candidate(fields=["machine-learning"]), limit=10)
    }
    assert tiers == {1: 0, 2: 1, 3: 2}


def test_a_far_match_is_described_as_a_sideways_move():
    (entry,) = rank_jobs(
        [FakeJob(1, "Software Engineer, New Grad")],
        Candidate(fields=["machine-learning"]),
        limit=1,
    )
    assert "sideways move into Software Engineering" in entry.explain()
    assert "related field" not in entry.explain()


# --------------------------------------------------------------------------
# The terms that the wider taxonomy made dangerous
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "must_not_be"),
    [
        # Terms match at a left word boundary but stay open on the right, so a short
        # term quietly swallows every longer word it heads. Each of these did.
        ("QA Automation Engineer", "audit-risk"),          # "assurance"
        ("Solutions Architect, Data", "civil-engineering"),  # "architect"
        ("Quantitative Strategy Developer", "consulting"),   # "strategy"
        ("Semiconductor Process Engineer", "marketing"),     # "sem"
        ("Shopify Developer", "retail"),                     # "shop"
        ("Data Warehouse Engineer", "supply-chain"),         # "warehouse"
        ("Device Driver Engineer", "transport"),             # "driver"
        ("Fleet Remediation Engineering", "transport"),      # "fleet"
    ],
)
def test_broad_terms_do_not_capture_unrelated_titles(title, must_not_be):
    assert must_not_be not in classify_title(title)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Machine Learning Engineer", "machine-learning"),
        ("Senior Quantity Surveyor", "civil-engineering"),
        ("Quantitative Researcher, Systematic Trading", "quantitative-finance"),
        ("Community Nurse", "healthcare"),
        ("Associate Legal Counsel", "legal"),
        ("Talent Acquisition Partner", "recruiting"),
        ("Bioprocess Engineer, Upstream", "biotech"),
        ("IT Support Engineer", "it-support"),
        ("Controls Engineer - Automation", "electrical-engineering"),
        ("Clinical Research Associate", "clinical-research"),
    ],
)
def test_the_new_fields_classify_the_titles_they_were_added_for(title, expected):
    assert expected in classify_title(title)


# --------------------------------------------------------------------------
# Reading a CV with a model
# --------------------------------------------------------------------------


def test_no_endpoint_configured_means_no_model_call(monkeypatch):
    """The floor. With nothing configured the rules carry the whole job."""
    monkeypatch.setattr(llm_profile.settings, "llm_api_key", "")
    monkeypatch.setattr(llm_profile.settings, "llm_fallback_api_key", "")
    assert llm_profile.available() is False
    assert llm_profile.read_cv("Machine learning engineer, PyTorch") is None


def test_a_local_endpoint_needs_no_key(monkeypatch):
    """Ollama on localhost has no notion of an API key."""
    monkeypatch.setattr(llm_profile.settings, "llm_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(llm_profile.settings, "llm_api_key", "")
    assert llm_profile.available() is True


def test_the_schema_can_only_ask_for_fields_that_exist():
    """Generated from FIELDS, so it cannot drift out of step with the taxonomy."""
    enum = llm_profile._schema()["properties"]["fields"]["items"]["enum"]
    assert set(enum) == set(FIELDS)


def test_the_catalogue_shows_the_model_every_field_under_its_group():
    catalogue = llm_profile._catalogue()
    for label in GROUP_LABELS.values():
        assert label in catalogue
    for key in FIELDS:
        assert key in catalogue


def test_an_invented_field_is_dropped_rather_than_searched():
    """The strict schema that would prevent this is exactly what some free providers
    do not support, so it is re-checked here."""
    reading = llm_profile._clean(
        {
            "fields": ["machine-learning", "quantum-alchemy", "machine-learning"],
            "skills": ["PyTorch", "pytorch", "  CUDA  "],
            "years_experience": 3,
            "seniority": "wizard",
            "summary": "Builds and ships models.",
        },
        "llama-3.3-70b",
    )
    assert reading.fields == ["machine-learning"]
    assert reading.skills == ["pytorch", "cuda"]
    assert reading.years_experience == 3
    assert reading.seniority is None


@pytest.mark.parametrize("years", [-1, 99, "three", None, True])
def test_an_unusable_years_figure_becomes_not_stated(years):
    """`True` is in here on purpose: bool is an int in Python, and a model that answers
    the question literally would otherwise be read as one year of experience."""
    payload = {"fields": [], "skills": [], "years_experience": years,
               "seniority": None, "summary": ""}
    assert llm_profile._clean(payload, "m").years_experience is None
