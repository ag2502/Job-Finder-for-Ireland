"""Reading a CV with a model rather than a keyword list.

`matching/resume.py` reads a CV by rule: it scans for known skill tokens and for lines
that look like job titles. That is fast, free and inspectable, and it stays as the
floor. What it cannot do is *understand* the document. A CV whose experience section
reads "trained a transformer on 40M product reviews; deployed the inference service on
GKE" contains no title line and none of the words "machine learning", so the rules see
a handful of skill tokens and no field at all. The searcher is then left to guess which
of fifty-odd boxes describes them, which is where the mismatch starts.

The model gets one job, and it is a narrow one: given the CV text, name the fields from
*this* taxonomy that the person should be searching in. It chooses from a closed list -
the schema's enum is generated from `FIELDS` - and everything it returns is intersected
with that list again on the way out. That framing is why a free open-weight model is
enough here: the task is classification into 56 known labels, not open generation, and
a wrong answer costs a worse ranking rather than a wrong fact on the page.

Three things make it safe to depend on nothing:

* Any OpenAI-compatible endpoint works - Cerebras, Groq, OpenRouter, Hugging Face,
  Cloudflare, or Ollama on localhost - because the request is plain JSON over httpx,
  which the deployment already carries for Supabase. No SDK, no extra bundle weight.
* A second endpoint can be configured and is tried when the first is rate-limited,
  which is how every free tier fails.
* If both are unset, unreachable, or wrong, `read_cv` returns None and the rule-based
  reading carries the whole job. A CV upload must never fail because somebody else's
  free tier ran out for the day.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache

import httpx

from jobfinder.core.config import settings
from jobfinder.normalize.taxonomy import FIELDS, GROUP_LABELS

logger = logging.getLogger(__name__)

# How much of the CV to send. Two dense pages is roughly 6k characters; the cap is
# generous enough for a long academic CV and small enough that one upload cannot burn
# a day's free allowance on its own.
MAX_CV_CHARS = 24_000

# Beyond this the "fields" answer stops being a search and starts being a shrug.
MAX_FIELDS = 5

SENIORITY_LEVELS = ("intern", "junior", "mid", "senior", "lead", "principal", "director")

_SYSTEM = """\
You read a CV and say which job fields the person should be searching in.

You are given a fixed list of fields. Choose only from it. Reply with JSON only.

Rules:
- Name the fields the person is actually employable in today, most apt first. Weigh the
  work they have done and the tools they have used far above job titles, which vary
  wildly between employers.
- Be specific. If someone builds and ships models, that is Machine Learning & AI, not
  Software Engineering. If they write services that happen to call a model, it is
  Software Engineering. Choosing the broad neighbour when the specific field fits is
  the single worst mistake you can make here: it buries the roles they want under the
  much larger pile of roles they do not.
- At most {max_fields} fields, and fewer is better. Two precise fields beat five vague
  ones. Only name a second or third field when the CV genuinely supports it - a student
  with one ML internship and a Java module has one field, not two.
- skills: the concrete technologies, tools, methods and domain systems named in the CV.
  Lowercase. Only what the document actually says; never infer a tool from a job title.
- years_experience: full-time professional years, excluding internships and study. A
  new graduate is 0. Use null if the CV does not support a figure.
- seniority: the level they should be applying at now, not the most senior word in the
  document.
- summary: one plain sentence naming what this person does, for them to read back and
  check. No flattery, no adjectives.
"""


@dataclass
class CvReading:
    """What the model made of the CV. Every field is advisory - the caller decides."""

    fields: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    years_experience: int | None = None
    seniority: str | None = None
    summary: str = ""
    model: str = ""


@dataclass(frozen=True)
class Endpoint:
    """One OpenAI-compatible chat-completions endpoint."""

    base_url: str
    model: str
    api_key: str

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


def endpoints() -> list[Endpoint]:
    """The endpoints to try, in order. Empty when the model path is switched off.

    A local Ollama needs no key, so a configured base URL is enough to count; every
    hosted provider needs one, and a base URL without a key there would only produce a
    401 per upload.
    """
    configured = [
        Endpoint(settings.llm_base_url, settings.llm_model, settings.llm_api_key),
        Endpoint(
            settings.llm_fallback_base_url,
            settings.llm_fallback_model or settings.llm_model,
            settings.llm_fallback_api_key,
        ),
    ]
    return [
        e for e in configured
        if e.base_url and e.model and (e.api_key or "localhost" in e.base_url
                                       or "127.0.0.1" in e.base_url)
    ]


def available() -> bool:
    """Whether a CV can be read by a model at all, without making a request."""
    return bool(endpoints())


def _schema() -> dict:
    """The response schema, with the field enum generated from the live taxonomy.

    Generated rather than hand-written on purpose: a hand-written copy is a second list
    of field keys to keep in step with `FIELDS`, and the day it drifts the model returns
    a key that classifies nothing and the search quietly gets worse.
    """
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "maxItems": MAX_FIELDS,
                "items": {"type": "string", "enum": sorted(FIELDS)},
            },
            "skills": {"type": "array", "maxItems": 60, "items": {"type": "string"}},
            "years_experience": {"type": ["integer", "null"]},
            "seniority": {"type": ["string", "null"], "enum": [*SENIORITY_LEVELS, None]},
            "summary": {"type": "string"},
        },
        "required": ["fields", "skills", "years_experience", "seniority", "summary"],
        "additionalProperties": False,
    }


def _catalogue() -> str:
    """The fields the model may choose from, under their group headings."""
    lines: list[str] = []
    for group, label in GROUP_LABELS.items():
        keys = [key for key, f in FIELDS.items() if f.group == group]
        if not keys:
            continue
        lines.append(f"{label}:")
        lines.extend(f"  {key} - {FIELDS[key].label}" for key in keys)
    return "\n".join(lines)


def read_cv(text: str) -> CvReading | None:
    """Ask a model which fields this CV belongs to, or None if it cannot be asked."""
    text = (text or "").strip()
    if not text or not available():
        return None
    return _read_cached(hashlib.sha256(text.encode()).hexdigest(), text[:MAX_CV_CHARS])


@lru_cache(maxsize=256)
def _read_cached(_digest: str, text: str) -> CvReading | None:
    """Keyed on the digest, so paging and re-sorting never pay for a second call."""
    prompt = f"Fields to choose from:\n{_catalogue()}\n\nCV:\n{text}"

    for endpoint in endpoints():
        payload = _ask(endpoint, prompt)
        if payload is not None:
            return _clean(payload, endpoint.model)
    return None


def _ask(endpoint: Endpoint, prompt: str) -> dict | None:
    """One endpoint, one answer, or None to let the caller try the next.

    Strict `json_schema` is asked for first because it is what actually holds an open
    model to the enum. Support is uneven across free providers, though, and the ones
    that lack it reject the whole request rather than ignoring the field - so a 400 is
    retried once in plain JSON mode, where `_clean` becomes the only thing standing
    between the model and the ranker. It is written to be exactly that.
    """
    body = {
        "model": endpoint.model,
        # Extraction, not composition: the same CV should produce the same fields on
        # Monday and on Friday.
        "temperature": 0,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": _SYSTEM.format(max_fields=MAX_FIELDS)},
            {"role": "user", "content": prompt},
        ],
    }
    formats = [
        {"type": "json_schema",
         "json_schema": {"name": "cv_reading", "strict": True, "schema": _schema()}},
        {"type": "json_object"},
    ]
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"

    for response_format in formats:
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                response = client.post(
                    endpoint.url, headers=headers,
                    json={**body, "response_format": response_format},
                )
            if response.status_code == 400 and response_format["type"] == "json_schema":
                logger.info("%s rejected json_schema; retrying in JSON mode",
                            endpoint.base_url)
                continue
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception:  # noqa: BLE001 - a CV upload must not fail on someone's API
            logger.warning("CV reading via %s (%s) failed", endpoint.base_url,
                           endpoint.model, exc_info=True)
            return None
    return None


def _clean(payload: dict, model: str) -> CvReading:
    """Trust the transport for shape, never for meaning.

    Everything the model returns is re-checked against the same tables the rest of the
    system uses, because the strict schema that would otherwise guarantee it is exactly
    the thing some free providers do not support.
    """
    fields: list[str] = []
    for key in payload.get("fields") or []:
        if key in FIELDS and key not in fields:
            fields.append(key)

    skills: list[str] = []
    for skill in payload.get("skills") or []:
        cleaned = str(skill).strip().lower()
        if cleaned and cleaned not in skills:
            skills.append(cleaned)

    years = payload.get("years_experience")
    if not isinstance(years, int) or isinstance(years, bool) or not 0 <= years <= 50:
        years = None

    seniority = payload.get("seniority")
    if seniority not in SENIORITY_LEVELS:
        seniority = None

    return CvReading(
        fields=fields[:MAX_FIELDS],
        skills=skills[:60],
        years_experience=years,
        seniority=seniority,
        summary=str(payload.get("summary") or "").strip()[:300],
        model=model,
    )
