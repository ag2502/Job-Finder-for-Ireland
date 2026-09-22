"""Company-name and advert-grouping rules.

These decide when two rows are copies of one advert. Getting them wrong is visible
either way: too loose and two employers merge into one listing, too tight and the same
job is offered three times over.
"""

from __future__ import annotations

import pytest

from jobfinder.normalize.dedup import (
    canonical_title,
    compute_dedup_key,
    normalize_company_name,
    same_employer,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Byrne Wallace Shields LLP", "byrne wallace shields"),
        ("Susquehanna International Group, LLP", "susquehanna"),
        ("Acme Teoranta", "acme"),
        ("Acme DAC", "acme"),
        ("Acme Pty Ltd", "acme"),
        ("Lidl Ireland", "lidl"),
    ],
)
def test_legal_suffixes_are_stripped(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # Words that double as ordinary vocabulary must survive, or unrelated
        # employers collide.
        "Dublin Spa",
        "Software as a Service",
    ],
)
def test_ordinary_words_are_not_treated_as_suffixes(raw: str) -> None:
    assert normalize_company_name(raw) == raw.lower()


def test_the_llp_variant_now_shares_a_dedup_key():
    """The reported duplicate: one advert listed under two spellings of one firm."""
    assert compute_dedup_key(
        "Byrne Wallace Shields LLP", "Graduate AI Automation Engineer", is_dublin=True
    ) == compute_dedup_key(
        "Byrne Wallace Shields", "Graduate AI Automation Engineer", is_dublin=True
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("byrne wallace", "byrne wallace shields"),
        ("mason hayes", "mason hayes curran"),
    ],
)
def test_a_longer_spelling_of_one_employer_matches(a: str, b: str) -> None:
    assert same_employer(a, b)


def test_a_one_word_name_never_absorbs_a_longer_one():
    """A deliberate gap, and the price of the guard above.

    "Susquehanna" and "Susquehanna Securities" are plainly one firm, but so are
    "Version" and "Version 1" not, and "bank of" and "Bank of America" emphatically
    not. One word is too little to identify an employer, so these stay separate and
    the rare genuine pair is listed twice rather than risking a wrong merge.
    """
    assert not same_employer("susquehanna", "susquehanna securities")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # "Bank of Ireland" normalises to the fragment "bank of", which would otherwise
        # prefix - and swallow - every other bank.
        ("bank of", "bank of america"),
        # A single shared first word is not an identity.
        ("version", "version 1"),
        ("stripe", "shopify"),
    ],
)
def test_unrelated_employers_do_not_match(a: str, b: str) -> None:
    assert not same_employer(a, b)


def test_title_inflections_and_noise():
    assert canonical_title("Backend Engineer (Remote)") == "backend engineer"
    assert canonical_title("Backend Engineer") == canonical_title("backend engineer")
