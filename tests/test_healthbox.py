"""HealthBox HR job feeds."""

from __future__ import annotations

from jobfinder.sources.healthbox import parse_feed

CARD = """
<div class="card"><div class="card-header"><h3>
<a href="https://app.hbhr.io/job-application/external/19f06930-5d577f44" target="_blank">Early Years Educator</a>
</h3></div><div class="card-body vacancy-item"><h5>
<span class="badge bg-light"><i class="fas fa-money-bill"></i> €15.00 - €17.50</span>
<span class="badge bg-light"><i class="fas fa-briefcase"></i> Full Time, Part Time, Permanent </span>
</h5><p>We are hiring Early Years Educators.</p></div></div>
<div class="card"><div class="card-body">No link here</div></div>
"""


def test_each_card_is_a_posting_with_its_hours():
    jobs = parse_feed(CARD, "Kildare, Ireland")
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.title, job.source_job_id, job.location_raw) == ("Early Years Educator", "19f06930-5d577f44", "Kildare, Ireland")
    assert job.employment_type == "Full Time, Part Time, Permanent"
    assert "Early Years Educators" in job.description


def test_a_town_in_the_title_gives_its_county():
    html = CARD.replace("Early Years Educator", "Brand New Crèche Opening in Leixlip")
    assert parse_feed(html, "Ireland")[0].location_raw == "Kildare, Ireland"
