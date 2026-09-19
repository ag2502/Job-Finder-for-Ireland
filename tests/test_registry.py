"""Universe import, ATS detection and the bulk sweep.

Coverage is `registry size x detection hit rate`, so these two steps decide how much of
Ireland the crawler can reach at all. Everything downstream is only as good as what
lands here.
"""

from __future__ import annotations

import gzip

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.core.models import Company, CoverageState, Source
from jobfinder.registry.bulk_detect import sweep
from jobfinder.registry.detect import (
    Budget,
    careers_links,
    detect_in_text,
    fetch_capped,
    slug_candidates,
)
from jobfinder.registry.universe import canonical_website, import_universe
from jobfinder.sources.base import build_client


# ---------------------------------------------------------------------------
# Universe import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("stripe.com", "https://stripe.com"),
        ("https://www.stripe.com/", "https://stripe.com"),
        ("http://STRIPE.com", "https://stripe.com"),
        ("https://stripe.com/careers?utm_source=x", "https://stripe.com"),
        ("", None),
        ("not-a-domain", None),
    ],
)
def test_website_canonicalisation(raw: str, expected: str | None):
    """Lists disagree about www, scheme and trailing slash for the same company."""
    assert canonical_website(raw) == expected


def _write(tmp_path, rows: str):
    path = tmp_path / "companies.csv"
    path.write_text("name,website,coverage_priority,seed_source\n" + rows, encoding="utf-8")
    return path


def test_import_adds_companies_awaiting_detection(session: Session, tmp_path):
    path = _write(tmp_path, "Acme Ltd,acme.ie,3,test\nBeta Corp,beta.com,4,test\n")
    stats = import_universe(session, path)

    assert stats.added == 2
    companies = session.execute(select(Company)).scalars().all()
    assert {c.coverage_state for c in companies} == {CoverageState.UNRESOLVED}


def test_reimport_never_resets_a_company_detection_already_resolved(
    session: Session, tmp_path
):
    """Re-running an import must not undo a resolved source, or the sweep is wasted."""
    path = _write(tmp_path, "Acme Ltd,acme.ie,3,test\n")
    import_universe(session, path)

    company = session.execute(select(Company)).scalar_one()
    company.coverage_state = CoverageState.ATS_DETECTED
    session.add(Source(company_id=company.id, adapter="greenhouse", slug="acme"))
    session.flush()

    import_universe(session, path)

    assert company.coverage_state is CoverageState.ATS_DETECTED
    assert session.execute(select(Source)).scalars().all()


def test_import_promotes_priority_but_never_demotes(session: Session, tmp_path):
    """A generic list must not bury a company a curated list marked important."""
    import_universe(session, _write(tmp_path, "Acme Ltd,acme.ie,1,curated\n"))
    company = session.execute(select(Company)).scalar_one()
    assert company.coverage_priority == 1

    import_universe(session, _write(tmp_path, "Acme Ltd,acme.ie,5,generic\n"))
    assert company.coverage_priority == 1


def test_same_company_in_two_spellings_is_one_row(session: Session, tmp_path):
    path = _write(
        tmp_path,
        "Acme Ltd,acme.ie,3,test\nAcme Limited,https://www.acme.ie/,3,test\n",
    )
    import_universe(session, path)
    assert len(session.execute(select(Company)).scalars().all()) == 1


def test_rows_without_a_usable_website_are_skipped_not_imported(
    session: Session, tmp_path
):
    path = _write(tmp_path, "No Website Co,,3,test\nGood Co,good.ie,3,test\n")
    stats = import_universe(session, path)

    assert stats.added == 1
    assert stats.skipped == 1


# ---------------------------------------------------------------------------
# Detection primitives
# ---------------------------------------------------------------------------


def test_vendor_footer_does_not_register_the_vendor_as_the_employer():
    """`{slug}.personio.de` also matches Personio's own "powered by" link."""
    assert detect_in_text('<a href="https://personio.jobs.personio.de/x">') is None
    assert detect_in_text('<a href="https://acme.jobs.personio.de/x">') == (
        "personio",
        "acme",
    )


def test_workday_compound_slug_is_assembled():
    markup = '<a href="https://acme.wd3.myworkdayjobs.com/en-US/AcmeCareers">'
    assert detect_in_text(markup) == ("workday", "acme:wd3:AcmeCareers")


def test_offsite_careers_link_outranks_an_onsite_one():
    """An off-site careers link usually *is* the ATS, which is the best case."""
    html = (
        '<a href="/about/careers">Careers</a>'
        '<a href="https://jobs.example.io/acme">Open roles</a>'
    )
    ranked = careers_links(html, "https://acme.ie")
    assert ranked[0] == "https://jobs.example.io/acme"


def test_social_and_vendor_marketing_links_are_never_the_careers_page():
    """Regression: the off-site bonus made a footer's social links the top pick.

    CPL's careers URL was recorded as its YouTube channel, Morgan McKinley's as its
    Facebook page, and Phorest's as Teamtailor's "powered by" marketing page - each
    off-site and mentioning jobs, so each outranked the real careers page.
    """
    html = (
        '<a href="https://www.facebook.com/acmejobs">Jobs at Acme</a>'
        '<a href="https://www.youtube.com/user/AcmeCareers">Careers channel</a>'
        '<a href="https://www.teamtailor.com/en/?utm_content=careers.acme.ie">'
        "Powered by Teamtailor careers</a>"
        '<a href="https://ie.indeed.com/cmp/acme/jobs">Jobs</a>'
        '<a href="/careers">Careers</a>'
    )
    assert careers_links(html, "https://acme.ie") == ["https://acme.ie/careers"]


def test_a_vendor_board_host_is_still_followed():
    """Only the vendor's marketing apex is excluded, never its board."""
    html = '<a href="https://apply.workable.com/acme/">See open roles</a>'
    assert careers_links(html, "https://acme.ie") == ["https://apply.workable.com/acme/"]


def test_slug_candidates_prefer_the_domain_then_the_name():
    """Datadog's domain is datadoghq.com but its board slug is datadog."""
    assert slug_candidates("https://datadoghq.com", "Datadog") == ["datadoghq", "datadog"]
    assert slug_candidates("https://stripe.com", "Stripe") == ["stripe"]


def test_budget_expires():
    assert Budget(seconds=0).expired is True
    assert Budget(seconds=30).expired is False


@respx.mock
def test_capped_fetch_does_not_double_decode_a_compressed_body():
    """Regression: every ATS fingerprint stopped matching on compressed sites.

    `iter_bytes()` yields already-decompressed bytes. Carrying `Content-Encoding: gzip`
    onto the rebuilt response makes it gunzip plain HTML, `.text` becomes mojibake, and
    detection silently fails on the majority of sites that compress.
    """
    markup = '<a href="https://boards.greenhouse.io/acme">Jobs</a>'
    respx.get("https://acme.ie/").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            content=gzip.compress(markup.encode()),
        )
    )

    with build_client() as client:
        response = fetch_capped(client, "https://acme.ie/")

    assert response.text == markup
    assert detect_in_text(response.text) == ("greenhouse", "acme")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def _pending(session: Session, name: str, website: str) -> Company:
    company = Company(
        name=name,
        normalized_name=name.lower().replace(" ", ""),
        website=website,
        coverage_state=CoverageState.UNRESOLVED,
        coverage_priority=3,
    )
    session.add(company)
    session.flush()
    return company


@respx.mock
def test_sweep_registers_a_detected_source(session: Session):
    _pending(session, "Acme", "https://acme.ie")
    respx.get("https://acme.ie").mock(
        return_value=httpx.Response(
            200, html='<a href="https://boards.greenhouse.io/acmeco">Careers</a>'
        )
    )

    stats = sweep(session, max_workers=1)

    assert stats.registered == 1
    source = session.execute(select(Source)).scalar_one()
    assert (source.adapter, source.slug) == ("greenhouse", "acmeco")


@respx.mock
def test_a_recognised_platform_with_no_adapter_is_recorded_not_registered(
    session: Session,
):
    """A source with no adapter fails every crawl until the breaker disables it."""
    _pending(session, "Taleo Co", "https://taleoco.ie")
    respx.get("https://taleoco.ie").mock(
        return_value=httpx.Response(
            200, html='<a href="https://taleoco.taleo.net/careersection/jobs">Careers</a>'
        )
    )

    stats = sweep(session, max_workers=1)

    assert stats.detected == 1
    assert stats.registered == 0
    assert stats.no_adapter == 1
    assert session.execute(select(Source)).scalars().all() == []


@respx.mock
def test_two_companies_resolving_to_one_board_register_it_once(session: Session):
    """A parent and its Irish subsidiary usually share a board."""
    _pending(session, "Parent", "https://parent.ie")
    _pending(session, "Subsidiary", "https://sub.ie")
    for host in ("https://parent.ie", "https://sub.ie"):
        respx.get(host).mock(
            return_value=httpx.Response(
                200, html='<a href="https://boards.greenhouse.io/shared">Careers</a>'
            )
        )

    stats = sweep(session, max_workers=1)

    assert stats.registered == 1
    assert stats.already_registered == 1
    assert len(session.execute(select(Source)).scalars().all()) == 1


@respx.mock
def test_a_company_with_a_careers_page_but_no_ats_keeps_its_link(session: Session):
    """BLOCKED is a work queue, not a dead end: the directory still links to it."""
    company = _pending(session, "Manual Co", "https://manual.ie")
    respx.get("https://manual.ie").mock(
        return_value=httpx.Response(200, html='<a href="/careers">Careers</a>')
    )
    respx.get("https://manual.ie/careers").mock(
        return_value=httpx.Response(200, html="<h1>Work with us</h1>")
    )
    respx.route(host="manual.ie").mock(return_value=httpx.Response(404))
    respx.route().mock(return_value=httpx.Response(404))

    sweep(session, max_workers=1)

    assert company.coverage_state is CoverageState.BLOCKED
    assert company.careers_url is not None


def test_a_company_that_already_has_a_source_is_not_re_detected(session: Session):
    company = _pending(session, "Done", "https://done.ie")
    session.add(Source(company_id=company.id, adapter="greenhouse", slug="done"))
    session.flush()

    assert sweep(session, max_workers=1).checked == 0


def test_a_dropped_connection_mid_write_does_not_abort_the_sweep(
    session: Session, monkeypatch
):
    """Regression: a dropped connection during the write phase crashed the whole sweep.

    `_apply_batch` retries an `OperationalError` by calling `session.invalidate()` to
    guarantee a fresh connection - but `invalidate()` also expunges every object the
    session knows about, detaching the `Company` rows the retry still holds in `by_id`.
    Reusing a detached instance raises `DetachedInstanceError` the moment an unloaded
    attribute is touched, which aborted the sweep entirely: not just this batch, but
    every batch after it, discarding a completed network probe of the whole registry
    over one flaky commit.
    """
    from sqlalchemy.exc import OperationalError

    from jobfinder.registry import bulk_detect

    acme = _pending(session, "Acme", "https://acme.ie")

    # Mimics the real failure: a genuine `Session.invalidate()` leaves an instance that
    # was mid-flush both expired (its column state must be reloaded) and detached (no
    # session left to reload it from) - the exact combination `_load_expired` raises
    # `DetachedInstanceError` on. Plain `expunge_all()` alone does not reproduce this,
    # because an instance's already-loaded attributes survive detachment untouched.
    def fake_invalidate():
        session.expire(acme)
        session.expunge_all()

    monkeypatch.setattr(session, "invalidate", fake_invalidate)

    real_commit = session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("commit", {}, Exception("connection lost"))
        return real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)

    def fake_detect(website, client=None, name=None):
        from jobfinder.registry.detect import Detection

        return Detection(
            adapter="greenhouse", slug="acmeco", careers_url=website, confidence="high"
        )

    monkeypatch.setattr(bulk_detect, "detect_for_website", fake_detect)

    stats = bulk_detect.sweep(session, max_workers=1)

    assert calls["n"] == 2  # the flaky commit actually fired and was retried
    assert stats.registered == 1
    source = session.execute(select(Source)).scalar_one()
    assert (source.adapter, source.slug) == ("greenhouse", "acmeco")


# ---------------------------------------------------------------------------
# Promoting blocked companies to generic extraction
# ---------------------------------------------------------------------------


def _blocked(session: Session, name: str, careers_url: str) -> Company:
    company = Company(
        name=name,
        normalized_name=name.lower().replace(" ", ""),
        website="https://" + name.lower().replace(" ", "") + ".ie",
        careers_url=careers_url,
        coverage_state=CoverageState.BLOCKED,
        coverage_priority=3,
    )
    session.add(company)
    session.flush()
    return company


def _ld(title: str, identifier: int) -> str:
    return (
        '<script type="application/ld+json">'
        f'{{"@type":"JobPosting","title":"{title}","identifier":{identifier}}}'
        "</script>"
    )


@respx.mock
def test_a_site_with_real_job_markup_is_promoted_to_generic_extraction(session: Session):
    from jobfinder.registry.extraction import promote_blocked

    company = _blocked(session, "Bespoke Co", "https://bespoke.ie/careers")
    respx.get("https://bespoke.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://bespoke.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://bespoke.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://bespoke.ie/careers").mock(
        return_value=httpx.Response(200, html=_ld("Engineer", 1) + _ld("Analyst", 2))
    )

    stats = promote_blocked(session, max_workers=1)

    assert stats.registered == 1
    assert company.coverage_state is CoverageState.GENERIC_EXTRACTION
    source = session.execute(select(Source)).scalar_one()
    assert source.adapter == "jsonld"
    assert source.slug == "https://bespoke.ie/careers"
    assert source.tier == 3


@respx.mock
def test_a_plain_vacancy_list_is_read_when_there_is_no_markup(session: Session, monkeypatch):
    """The page `jsonld` cannot read falls through to reading its HTML."""
    from jobfinder.registry.extraction import promote_blocked
    from jobfinder.sources.base import BaseAdapter

    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))
    company = _blocked(session, "Council Co", "https://council.ie/careers")
    respx.get("https://council.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://council.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://council.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://council.ie/careers").mock(
        return_value=httpx.Response(
            200,
            html='<ul><li><a href="/vacancy/1">Executive Engineer</a></li>'
            '<li><a href="/vacancy/2">Assistant Staff Officer</a></li></ul>',
        )
    )
    for n in (1, 2):
        respx.get(f"https://council.ie/vacancy/{n}").mock(
            return_value=httpx.Response(
                200, html="<main><h1>Role</h1><p>Location: Cork</p><a>Apply</a></main>"
            )
        )

    stats = promote_blocked(session, max_workers=1)

    assert stats.registered == 1
    assert stats.by_adapter == {"careers_html": 1}
    assert company.coverage_state is CoverageState.GENERIC_EXTRACTION
    source = session.execute(select(Source)).scalar_one()
    assert (source.adapter, source.slug, source.tier) == (
        "careers_html", "https://council.ie/careers", 3,
    )


@respx.mock
def test_two_companies_sharing_one_careers_page_register_it_once(session: Session):
    """Regression: the second registration raised IntegrityError and killed the run.

    A parent and its Irish arm usually share a careers page, and detection hands both
    the same URL. `sources` is unique on (adapter, slug), so registering it twice does
    not merely duplicate a row - it fails the next flush and takes down the whole
    extraction pass, including every company queued behind it.
    """
    from jobfinder.registry.extraction import promote_blocked

    parent = _blocked(session, "Amgen", "https://careers.amgen.com/en")
    irish = _blocked(session, "Amgen Ireland", "https://careers.amgen.com/en")
    respx.get("https://careers.amgen.com/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://careers.amgen.com/sitemap.xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://careers.amgen.com/sitemap_index.xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://careers.amgen.com/en").mock(
        return_value=httpx.Response(200, html=_ld("Engineer", 1) + _ld("Analyst", 2))
    )

    stats = promote_blocked(session, max_workers=1)

    assert stats.registered == 1
    assert stats.already_registered == 1
    # Both are covered: the page is crawled once and serves each of them.
    assert parent.coverage_state is CoverageState.GENERIC_EXTRACTION
    assert irish.coverage_state is CoverageState.GENERIC_EXTRACTION
    assert len(session.execute(select(Source)).scalars().all()) == 1


@respx.mock
def test_a_site_with_no_usable_markup_is_left_blocked(session: Session):
    """Registering it anyway adds a source that fails every run until the breaker trips."""
    from jobfinder.registry.extraction import promote_blocked

    company = _blocked(session, "Plain Co", "https://plain.ie/careers")
    respx.get("https://plain.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://plain.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://plain.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://plain.ie/careers").mock(
        return_value=httpx.Response(200, html="<h1>Email us your CV</h1>")
    )

    stats = promote_blocked(session, max_workers=1)

    assert stats.registered == 0
    assert company.coverage_state is CoverageState.BLOCKED
    assert session.execute(select(Source)).scalars().all() == []


@respx.mock
def test_a_single_stray_posting_is_not_treated_as_a_job_board(session: Session):
    """One JobPosting block on an "about us" page would otherwise register a source
    that reports the same phantom role forever."""
    from jobfinder.registry.extraction import promote_blocked

    company = _blocked(session, "Stray Co", "https://stray.ie/careers")
    respx.get("https://stray.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://stray.ie/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://stray.ie/sitemap_index.xml").mock(return_value=httpx.Response(404))
    respx.get("https://stray.ie/careers").mock(
        return_value=httpx.Response(200, html=_ld("The Only Role", 1))
    )

    stats = promote_blocked(session, max_workers=1)

    assert stats.registered == 0
    assert stats.empty == 1
    assert company.coverage_state is CoverageState.BLOCKED


def test_a_wedged_probe_does_not_stop_the_sweep(session: Session, monkeypatch):
    """Regression: three consecutive full sweeps died on one unresponsive host.

    Some hosts hang in a way no HTTP timeout covers — name resolution above all, since
    `getaddrinfo` is a blocking C call that ignores every timeout Python can set. Such a
    worker cannot be cancelled, so the sweep must be able to *abandon* it and finish
    with what it has, rather than joining it and never returning.
    """
    import threading

    from jobfinder.registry import bulk_detect
    from jobfinder.registry.detect import Detection

    _pending(session, "Wedged Co", "https://wedged.ie")
    _pending(session, "Fine Co", "https://fine.ie")

    wedged = threading.Event()

    def fake_detect(website, client=None, name=None):
        if "wedged" in website:
            wedged.wait()  # never set: models an uninterruptible blocking call
        return Detection(
            adapter="greenhouse", slug="fineco", careers_url=website, confidence="high"
        )

    monkeypatch.setattr(bulk_detect, "detect_for_website", fake_detect)

    stats = bulk_detect.sweep(session, max_workers=2, deadline_seconds=2.0)

    # The healthy company still resolves; the wedged one is simply not reported and
    # keeps its state for the next run.
    assert stats.registered == 1
    assert stats.checked == 1


# ---------------------------------------------------------------------------
# Slug probing must not misattribute another company's board
# ---------------------------------------------------------------------------


@respx.mock
def test_slug_probe_rejects_a_board_owned_by_a_different_company():
    """Regression: `meta.recruitee.com` was registered as Meta's board.

    It is a real, populated Recruitee board — belonging to an unrelated company that
    happens to share a short name. Probing is inference from a name collision, so where
    the platform states who owns a board, that statement decides. Misattributing an
    employer is worse than missing one: the jobs look legitimate and the user cannot
    tell they belong to someone else.
    """
    from jobfinder.registry.detect import probe_platforms

    board = {"offers": [{"id": 1, "title": "Engineer", "company_name": "Metafoor BV"}]}
    respx.get("https://meta.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=board)
    )
    respx.route().mock(return_value=httpx.Response(404))

    assert probe_platforms(["meta"], expected_name="Meta").found is False


@respx.mock
def test_slug_probe_accepts_a_board_that_names_the_right_company():
    from jobfinder.registry.detect import probe_platforms

    board = {"offers": [{"id": 1, "title": "Engineer", "company_name": "Acme Group Ireland Ltd"}]}
    respx.get("https://acmecorp.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=board)
    )
    respx.route().mock(return_value=httpx.Response(404))

    result = probe_platforms(["acmecorp"], expected_name="Acme")
    assert (result.adapter, result.slug) == ("recruitee", "acmecorp")


@respx.mock
def test_an_unverifiable_short_slug_is_refused():
    """Greenhouse does not say who owns a board, so a four-letter match is a coin flip."""
    from jobfinder.registry.detect import probe_platforms

    respx.get("https://boards-api.greenhouse.io/v1/boards/meta/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1, "title": "Engineer"}]})
    )
    respx.route().mock(return_value=httpx.Response(404))

    assert probe_platforms(["meta"], expected_name="Meta").found is False


@respx.mock
def test_an_unverifiable_but_distinctive_slug_is_accepted():
    from jobfinder.registry.detect import probe_platforms

    respx.get("https://boards-api.greenhouse.io/v1/boards/intercom/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1, "title": "Engineer"}]})
    )
    respx.route().mock(return_value=httpx.Response(404))

    result = probe_platforms(["intercom"], expected_name="Intercom")
    assert (result.adapter, result.slug) == ("greenhouse", "intercom")


@pytest.mark.parametrize(
    "markup,expected",
    [
        (
            # Oracle now has an adapter, which needs the host and site, not the pod.
            "https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/X",
            ("oracle_recruiting", "eofe.fa.us2.oraclecloud.com|X"),
        ),
        ("https://bostonscientific.eightfold.ai/careers", ("eightfold", "bostonscientific")),
        ("https://acme.taleo.net/careersection/x", ("taleo", "acme")),
        ("https://acme.icims.com/jobs", ("icims", "acme")),
        # Hosts found behind the careers pages left blocked.
        ("https://nuritas.hirehive.com/", ("hirehive", "nuritas")),
        ("https://titanhq.occupop-careers.com/", ("occupop", "titanhq")),
        ("https://viatel.peoplehr.net/Pages/JobBoard", ("peoplehr", "viatel")),
        ("https://bnm.keyhire.ie/", ("keyhire", "bnm")),
        ("https://acme.breezy.hr/", ("breezy", "acme")),
        ("https://jobs.jobvite.com/acme/jobs", ("jobvite", "acme")),
        (
            "https://dunnes.tal.net/vx/lang-en-GB/candidate/jobboard/vacancy/3/adv/",
            ("oleeo", "dunnes.tal.net|3"),
        ),
        (
            "https://www.candidatemanager.net/cm/p/pJobs.aspx?mid=YUYF&sid=BDCXCX",
            ("candidatemanager", "YUYF|BDCXCX"),
        ),
    ],
)
def test_platforms_without_adapters_are_still_named(markup: str, expected: tuple):
    """Recognising a platform we cannot yet crawl is worth more than it looks.

    A company recorded as being on Oracle Recruiting Cloud is a known quantity with a
    known cost to support. The same company recorded as an anonymous "blocked" is
    indistinguishable from a site with no careers page at all, so `jobfinder coverage`
    can say *which* missing adapter would buy the most coverage instead of leaving it
    to guesswork.
    """
    assert detect_in_text(markup) == expected


@respx.mock
def test_a_named_platform_with_no_adapter_is_not_registered_as_a_source(session: Session):
    from jobfinder.registry.bulk_detect import sweep

    _pending(session, "Avature Shop", "https://ashop.ie")
    respx.get("https://ashop.ie").mock(
        return_value=httpx.Response(
            200,
            html='<a href="https://ashop.avature.net/careers">Careers</a>',
        )
    )

    stats = sweep(session, max_workers=1)

    assert stats.detected == 1
    assert stats.no_adapter == 1
    assert session.execute(select(Source)).scalars().all() == []


@respx.mock
def test_an_extra_word_in_the_board_owners_name_means_a_different_employer():
    """Regression: `icon.recruitee.com` was registered as ICON plc's board.

    It belongs to "Icon Talent", an unrelated company. A subset-of-words rule accepts
    that pairing, because {icon} is a subset of {icon, talent} — but normalization has
    already stripped the words that would legitimately differ between a legal name and a
    careers-page name, so a *remaining* extra word is evidence of a different employer
    rather than a fuller description of the same one.
    """
    from jobfinder.registry.detect import probe_platforms

    board = {"offers": [{"id": 1, "title": "Engineer", "company_name": "Icon Talent"}]}
    respx.get("https://icon.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, json=board)
    )
    respx.route().mock(return_value=httpx.Response(404))

    assert probe_platforms(["icon"], expected_name="ICON plc").found is False


@respx.mock
def test_personio_feeds_are_checked_against_their_declared_subcompany():
    """`teamwork.jobs.personio.de` belongs to "Teamwork Crew Ltd", not Teamwork.com."""
    from jobfinder.registry.detect import probe_platforms

    feed = (
        "<workzag-jobs><position><id>1</id>"
        "<subcompany>Teamwork Crew Ltd</subcompany>"
        "<name>Engineer</name></position></workzag-jobs>"
    )
    respx.get("https://teamwork.jobs.personio.de/xml").mock(
        return_value=httpx.Response(200, text=feed)
    )
    respx.route().mock(return_value=httpx.Response(404))

    assert probe_platforms(["teamwork"], expected_name="Teamwork").found is False


@pytest.mark.parametrize(
    "declared,expected,same",
    [
        ("EY", "EY Ireland", True),
        ("Grant Thornton", "Grant Thornton Ireland", True),
        ("Acme Group Ireland Ltd", "Acme", True),
        ("Icon Talent", "ICON plc", False),
        ("Metafoor BV", "Meta", False),
        ("Teamwork Crew Ltd", "Teamwork", False),
    ],
)
def test_company_name_equivalence(declared: str, expected: str, same: bool):
    from jobfinder.registry.detect import _same_company

    assert _same_company(declared, expected) is same


def test_an_oracle_careers_url_yields_a_usable_host_and_site_slug():
    """The old fingerprint captured only the pod, which no request can address."""
    markup = (
        '<a href="https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/'
        'BNY-Careers">Careers</a>'
    )
    assert detect_in_text(markup) == (
        "oracle_recruiting",
        "eofe.fa.us2.oraclecloud.com|BNY-Careers",
    )


@respx.mock
def test_a_supported_platform_is_registered_only_once_a_real_fetch_returns_jobs(
    session: Session,
):
    """Detection's slug for a newly supported platform must prove itself before it lands.

    These fingerprints predate their adapters and do not always capture what the adapter
    addresses. An unproven slug would add a source that fails every crawl.
    """
    _pending(session, "Empty Tailor", "https://emptytailor.ie")
    respx.get("https://emptytailor.ie").mock(
        return_value=httpx.Response(
            200, html='<a href="https://emptytailor.teamtailor.com/jobs">Careers</a>'
        )
    )
    respx.get("https://emptytailor.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(404)
    )

    stats = sweep(session, max_workers=1)

    assert stats.detected == 1
    assert stats.rejected_slug == 1
    assert session.execute(select(Source)).scalars().all() == []


@respx.mock
def test_a_supported_platform_with_a_live_board_is_registered(session: Session):
    _pending(session, "Live Tailor", "https://livetailor.ie")
    respx.get("https://livetailor.ie").mock(
        return_value=httpx.Response(
            200, html='<a href="https://livetailor.teamtailor.com/jobs">Careers</a>'
        )
    )
    respx.get("https://livetailor.teamtailor.com/jobs.rss").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<rss><channel><item><title>Engineer</title>"
                "<link>https://livetailor.teamtailor.com/jobs/1-engineer</link>"
                "</item></channel></rss>"
            ),
        )
    )

    stats = sweep(session, max_workers=1)

    assert stats.registered == 1
    source = session.execute(select(Source)).scalar_one()
    assert (source.adapter, source.slug) == ("teamtailor", "livetailor")


@pytest.mark.parametrize("site", ["Careers", "jobs", "External"])
def test_a_workday_site_with_a_common_name_is_still_detected(site: str):
    """Regression: the single-slug blocklist was applied to Workday's site segment.

    "careers" and "jobs" are boilerplate on a Greenhouse or Lever URL but ordinary site
    names on Workday, so `broadridge.wd5.myworkdayjobs.com/Careers` was never detected.
    """
    markup = f'<a href="https://broadridge.wd5.myworkdayjobs.com/{site}">Open roles</a>'
    assert detect_in_text(markup) == ("workday", f"broadridge:wd5:{site}")


def test_a_vendor_cdn_host_is_not_taken_for_a_customer_board():
    markup = (
        '<script src="https://cdn1.hirehive.com/widget.js"></script>'
        '<a href="https://nuritas.hirehive.com/">Vacancies</a>'
    )
    assert detect_in_text(markup) == ("hirehive", "nuritas")


def test_the_generic_blocklist_still_applies_to_single_slug_boards():
    assert detect_in_text('<a href="https://boards.greenhouse.io/careers">x</a>') is None
