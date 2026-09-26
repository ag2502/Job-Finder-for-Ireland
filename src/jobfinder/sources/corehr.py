"""CoreHR e-recruitment adapter.

Most Irish universities, and a good part of the public sector, recruit through CoreHR,
each on its own tenant of one shared host:

    POST https://my.corehr.com/pls/{tenant}/erq_search_version_4.start_search_with_params

An empty search lists every open vacancy, a page at a time, under "Your search returned N
results". The form has to be sent whole: leaving out its blank fields, or
`p_competition_type=ALLOPTIONS`, earns a 403. Later pages are the same form with
`p_refresh_search=Y` and the `p_start_from` that the page's own "Next" form carries.

The slug is ``tenant|company|place``: the tenant (``ucdrecruit``), the company number
the tenant's own links use (``1`` for most, ``5023`` for UCC), and where the institution
is, since a card gives a reference, closing date, salary and department but seldom a
place. A job's own page opens only by posting a form, so there is no link to it; the
posting links to the tenant's vacancy search and names its reference.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

HOST = "https://my.corehr.com/pls"
MAX_PAGES = 40
COMPLETENESS = 0.9

FORM_FIELDS = (
    "p_recruitment_id", "p_keywords", "p_search_company", "p_position", "p_position_type",
    "p_department", "p_management_unit", "p_description", "p_location", "p_division",
    "p_pay_scale", "p_user_field1", "p_user_field2", "p_user_field3", "p_user_field4",
    "p_user_field5", "p_emp_status", "p_emp_substatus", "p_category", "p_sub_category",
    "p_job_category",
)

_TOTAL = re.compile(r"returned\s*(\d+)\s*result", re.I)
_ROW = re.compile(r'class="erq_searchv4_result_row"')
_TITLE = re.compile(r"<a\b[^>]*viewTheJobSpec\('([^']+)'\)[^>]*>(.*?)</a>", re.S)
_NEXT = re.compile(
    r'<form[^>]*name="searchv4navigateresultsforward".*?name="p_start_from"[^>]*value="(\d+)"', re.S
)
_NOISE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_LABELS = {
    "reference": ("Job Ref", "Job Reference"),
    "closing": ("Closing Date", "Close Date"),
    "salary": ("Salary",),
    "department": ("Dept", "Department", "School/Unit"),
    "location": ("Location", "Work Location", "Campus"),
}


def split_slug(slug: str) -> tuple[str, str, str | None]:
    tenant, company, place = (slug.split("|") + ["", ""])[:3]
    if not tenant:
        raise ValueError(f"CoreHR slug must be 'tenant|company|place', got {slug!r}")
    return tenant, company or "1", place or None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", fragment))).strip()


def _field(block: str, labels: tuple[str, ...]) -> str | None:
    """The first text after a label, whether it shares the label's cell or has its own."""
    for label in labels:
        match = re.search(rf">\s*{re.escape(label)}\s*:?\s*(?:<[^>]+>\s*)*([^<]+)", block)
        if match:
            value = _text(match.group(1)).strip(" :")
            if value:
                return value
    return None


def parse_page(page: str) -> tuple[list[dict], int | None, int | None]:
    """The vacancy cards on one results page, the stated total, and the next offset.

    The page size is the tenant's own (Maynooth shows eight, UCD ten), so the next page
    is found from the page's own "Next" form rather than assumed.
    """
    page = _NOISE.sub("", page)
    total_match = _TOTAL.search(page)
    total = int(total_match.group(1)) if total_match else None
    next_match = _NEXT.search(page)
    next_start = int(next_match.group(1)) if next_match else None

    starts = [m.start() for m in _ROW.finditer(page)]
    cards = []
    for index, start in enumerate(starts):
        block = page[start: starts[index + 1] if index + 1 < len(starts) else len(page)]
        title = _TITLE.search(block)
        if not title:
            continue
        rest = block[: title.start()] + block[title.end():]
        card = {"id": title.group(1), "title": _text(title.group(2))}
        for key, labels in _LABELS.items():
            value = _field(rest, labels)
            # Some tenants write the closing date with a script, leaving only "GMT".
            if value and not (key == "closing" and not re.search(r"\d", value)):
                card[key] = value
        cards.append(card)
    return cards, total, next_start


class CoreHRAdapter(BaseAdapter):
    name = "corehr"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        tenant, company, place = split_slug(slug)
        base = {
            "p_company": company, "p_internal_external": "E", "p_display_in_irish": "N",
            "p_competition_type": "ALLOPTIONS", "p_force_type": "E",
            **{field: "" for field in FORM_FIELDS},
        }
        search_page = f"{HOST}/{tenant}/erq_search_package.search_form?p_company={company}&p_internal_external=E"
        action = f"{HOST}/{tenant}/erq_search_version_4.start_search_with_params"

        cards: dict[str, dict] = {}
        total: int | None = None
        start: int | None = None
        for _ in range(MAX_PAGES):
            data = dict(base)
            if start is not None:
                data.update(p_refresh_search="Y", p_start_from=str(start), p_direction="N")
            response = client.post(action, data=data, headers={"Referer": search_page})
            response.raise_for_status()
            page, stated, start = parse_page(response.text)
            if total is None:
                if stated is None:
                    raise ValueError(f"CoreHR tenant {tenant} returned no search results page")
                total = stated
            before = len(cards)
            for card in page:
                cards.setdefault(card["id"], card)
            if len(cards) >= total or len(cards) == before or start is None:
                break
            self.polite_pause()
        else:
            return PartialJobs(self._build(cards, search_page, place))

        if total and len(cards) < total * COMPLETENESS:
            raise ValueError(f"CoreHR read {len(cards)} of {total} vacancies from {tenant}")
        return self._build(cards, search_page, place)

    @staticmethod
    def _build(cards: dict[str, dict], search_page: str, place: str | None) -> list[RawJob]:
        jobs = []
        for card in cards.values():
            details = [
                f"{label}: {card[key]}"
                for key, label in (("reference", "Job reference"), ("closing", "Closing date"),
                                   ("salary", "Salary"), ("department", "Department"))
                if card.get(key)
            ]
            jobs.append(RawJob(
                source_job_id=card["id"],
                title=card["title"],
                url=search_page,
                location_raw=card.get("location") or place,
                description="\n".join(details) or None,
                department=card.get("department"),
            ))
        return jobs


register(CoreHRAdapter())
