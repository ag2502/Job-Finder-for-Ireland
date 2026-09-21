"""Experience extraction and eligibility filtering."""

from __future__ import annotations

import pytest

from jobfinder.normalize.experience import (
    analyze,
    extract_min_years,
    infer_years_from_title,
    matches_experience,
)


# --------------------------------------------------------------------------
# Extracting a stated requirement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3+ years of experience in Python", 3),
        ("5 years' experience required", 5),
        ("Minimum of 4 years in a similar role", 4),
        ("At least 7 years of professional experience", 7),
        ("2-3 years of industry experience", 2),      # lower bound of a range
        ("2 to 4 years of relevant experience", 2),
        ("10+ years of experience in B2B sales", 10),
        ("0-2 years of experience", 0),
    ],
)
def test_extract_stated_years(text: str, expected: int) -> None:
    assert extract_min_years(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We have been in business for 30 years",   # company history, not a requirement
        "A 12 month contract",
        "Founded 5 years ago",
        "",
        None,
    ],
)
def test_years_not_tied_to_experience_are_ignored(text: str | None) -> None:
    assert extract_min_years(text) is None


def test_lowest_stated_figure_wins():
    """Ads list a general bar alongside higher figures for specific niches."""
    text = "5+ years of experience overall. 2+ years of experience with Kubernetes."
    assert extract_min_years(text) == 2


# --------------------------------------------------------------------------
# Inferring from the title
# --------------------------------------------------------------------------


def test_seniority_inference():
    assert infer_years_from_title("Senior Software Engineer") == 5
    assert infer_years_from_title("Principal Engineer") == 8
    assert infer_years_from_title("Director, Partnerships") == 10
    assert infer_years_from_title("Graduate Software Engineer") == 0
    assert infer_years_from_title("Software Engineer") is None


@pytest.mark.parametrize(
    "title",
    [
        "Customer Success Manager",
        "Project Manager",
        "Product Manager",
        "Account Manager",
        "Field Marketing Manager, SMB",
    ],
)
def test_bare_manager_is_not_a_seniority_signal(title: str) -> None:
    """"Manager" here names a job function, not a level.

    Treating it as senior wrongly inferred six years for entry-level roles and hid
    them from the people they were meant for.
    """
    assert infer_years_from_title(title) is None


def test_staff_titles_infer_through_the_specialism():
    """"Staff" is separated from its noun in nearly every real title, and the
    adjacent-words pattern matched none of them. Since only ~66% of adverts state a
    number, that miss was the whole floor: these roles stored no minimum and so were
    shown to searchers with one year of experience.
    """
    assert infer_years_from_title("Staff Backend Engineer, Datalake Platform") == 8
    assert infer_years_from_title("Staff Software Engineer") == 8
    assert infer_years_from_title("Staff Web Automation Engineer") == 8
    assert infer_years_from_title("Staff Engineer") == 8


def test_architect_titles_infer():
    assert infer_years_from_title("Solutions Architect") == 6
    assert infer_years_from_title("Enterprise Architect") == 6
    assert infer_years_from_title("Architect") == 5
    # An explicitly early-career architect role is still early-career.
    assert infer_years_from_title("Graduate Solutions Architect") == 0


def test_compound_leadership_titles_still_infer():
    assert infer_years_from_title("Engineering Manager") == 6
    assert infer_years_from_title("Software Development Manager") == 6
    assert infer_years_from_title("Manager, Software Engineering") == 6
    assert infer_years_from_title("Tech Lead") == 6


# --------------------------------------------------------------------------
# Combining the two
# --------------------------------------------------------------------------


def test_title_acts_as_a_floor_on_a_low_stated_figure():
    """Regression: a Staff Engineer advert whose lowest bullet said "2+ years with
    Kubernetes" was read as a two-year role and shown to juniors."""
    profile = analyze(
        "Staff Engineer, Site Reliability",
        "7+ years of experience overall. 2+ years of experience with Kubernetes.",
    )
    assert profile.min_years == 8  # the Staff floor, not the 2-year bullet


def test_stated_figure_wins_when_higher_than_the_title_floor():
    profile = analyze("Senior Marketing Manager", "10+ years of experience in B2B")
    assert profile.min_years == 10
    assert profile.inferred is False


def test_inferred_flag_marks_a_guess():
    assert analyze("Senior Engineer", "No numbers here").inferred is True
    assert analyze("Engineer", "3+ years of experience").inferred is False


def test_unknown_stays_unknown():
    profile = analyze("Software Engineer", "Come build things with us")
    assert profile.min_years is None
    assert profile.inferred is False


# --------------------------------------------------------------------------
# Internships and graduate roles
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer, Intern (Summer or Winter)",
        "Operations & Logistics Internship",
        "2027 Software Dev Engineer Intern",
        "Summer Analyst, Markets",
    ],
)
def test_internships_detected(title: str) -> None:
    profile = analyze(title)
    assert profile.is_internship
    assert profile.min_years == 0


def test_internal_does_not_match_intern():
    assert not analyze("Internal Audit Manager").is_internship
    assert not analyze("Internal Communications Lead").is_internship


def test_a_mention_of_an_intern_programme_is_not_an_internship():
    """Otherwise every advert boasting about its intern scheme becomes an internship."""
    profile = analyze(
        "Senior Software Engineer",
        "You will mentor our interns and run the internship programme.",
    )
    assert not profile.is_internship


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer, New Grad",
        "Graduate Programme 2027",
        "Software Engineer, Early Career (AI)",
        "Entry-Level Data Analyst",
        "Graduate Software Engineer",
        "2027 Future Leaders Academy: ROI Audit & Assurance Graduate Opportunities",
        "Trainee Accountant",
    ],
)
def test_graduate_roles_detected(title: str) -> None:
    profile = analyze(title)
    assert profile.is_graduate
    assert profile.min_years == 0


@pytest.mark.parametrize(
    "title",
    ["Graduate Recruiter", "Graduate Talent Acquisition Partner", "Postgraduate Researcher"],
)
def test_graduate_recruiting_jobs_are_not_graduate_roles(title: str) -> None:
    """The person hiring graduates is not a graduate hire."""
    assert not analyze(title).is_graduate


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


def _eligible(
    years, *, job_years=None, intern=False, grad=False, want_interns=False, want_grads=False
):
    return matches_experience(
        job_min_years=job_years,
        job_is_internship=intern,
        job_is_graduate=grad,
        candidate_years=years,
        want_internships=want_interns,
        want_graduate=want_grads,
    )


def test_no_stated_experience_returns_everything():
    """The requirement: say nothing, see all active openings."""
    assert _eligible(None, job_years=0)
    assert _eligible(None, job_years=10)
    assert _eligible(None, job_years=None)
    assert _eligible(None, grad=True)


def test_jobs_asking_for_more_are_excluded():
    assert not _eligible(3, job_years=5)
    assert not _eligible(0, job_years=1)


def test_jobs_at_or_below_are_included():
    assert _eligible(5, job_years=5)
    assert _eligible(5, job_years=3)
    assert _eligible(5, job_years=0)


def test_unknown_requirement_is_never_disqualifying():
    """Most adverts state no figure; excluding them would discard the majority."""
    assert _eligible(1, job_years=None)
    assert _eligible(20, job_years=None)


def test_graduate_roles_included_at_two_years_or_under():
    for years in (0, 1, 2):
        assert _eligible(years, grad=True, job_years=0), f"failed at {years}"


def test_graduate_roles_hidden_above_two_years():
    assert not _eligible(3, grad=True, job_years=0)
    assert not _eligible(10, grad=True, job_years=0)


def test_internships_are_opt_in_only():
    # Never mixed into a general search, at any experience level.
    assert not _eligible(None, intern=True)
    assert not _eligible(0, intern=True)
    assert not _eligible(1, intern=True)


def test_internships_only_returns_internships():
    assert _eligible(None, intern=True, want_interns=True)
    assert _eligible(5, intern=True, want_interns=True)
    # ...and nothing else.
    assert not _eligible(None, job_years=0, want_interns=True)
    assert not _eligible(None, grad=True, want_interns=True)


def test_graduate_only_returns_graduate_roles():
    # Experience is ignored: a graduate programme is an entry point by definition.
    assert _eligible(None, grad=True, job_years=0, want_grads=True)
    assert _eligible(6, grad=True, job_years=0, want_grads=True)
    # ...and nothing else, internships included.
    assert not _eligible(None, job_years=0, want_grads=True)
    assert not _eligible(None, job_years=None, want_grads=True)
    assert not _eligible(None, intern=True, want_grads=True)


def test_both_early_career_switches_mean_either():
    assert _eligible(None, intern=True, want_interns=True, want_grads=True)
    assert _eligible(None, grad=True, want_interns=True, want_grads=True)
    assert not _eligible(None, job_years=0, want_interns=True, want_grads=True)
