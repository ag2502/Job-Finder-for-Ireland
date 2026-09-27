"""The advert text as the job panel lays it out."""

from __future__ import annotations

from jobfinder.web.advert import Block, blocks


def test_blank_lines_separate_paragraphs():
    text = "Minimum qualifications:\n\nBachelor's degree.\n\n11 years of experience."
    assert blocks(text) == [
        Block("h", "Minimum qualifications"),
        Block("p", "Bachelor's degree."),
        Block("p", "11 years of experience."),
    ]


def test_a_sentence_split_across_lines_is_joined_back():
    """Workday breaks lines mid-sentence: "Contract" then ": Full-time role"."""
    text = "Contract\n: Full-time role - 40 hours per week\nWe are looking for\ncandidates with strong knowledge."
    out = blocks(text)
    assert Block("p", "Contract: Full-time role - 40 hours per week") in out
    assert Block("p", "We are looking for candidates with strong knowledge.") in out


def test_bullets_become_list_items_whatever_mark_they_use():
    text = "Requirements\n• Python\n- SQL\n* Kafka\n1. Terraform"
    assert blocks(text) == [
        Block("h", "Requirements"),
        Block("li", "Python"),
        Block("li", "SQL"),
        Block("li", "Kafka"),
        Block("li", "Terraform"),
    ]


def test_a_long_line_ending_in_a_colon_is_not_a_heading():
    line = "In this role you will work closely with product, design and data teams across Europe on:"
    assert blocks(line) == [Block("p", line)]


def test_nothing_is_dropped_or_reworded():
    text = "About the job\nWe build payments.\n\nWhy join us\nPension, health cover."
    words = " ".join(b.text for b in blocks(text)).split()
    assert words == "About the job We build payments. Why join us Pension, health cover.".split()


def test_empty_and_missing_text():
    assert blocks(None) == []
    assert blocks("  \n\n ") == []


def test_a_trailing_heading_with_nothing_under_it_is_dropped():
    assert blocks("We build payments.\nBenefits:") == [Block("p", "We build payments.")]
