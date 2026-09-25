"""Free language models for tailoring, tried in order until one answers.

Every provider here speaks the OpenAI chat-completions dialect, so one plain httpx call
covers all of them and the deployment carries no SDK. The order is set in
`core/config.py`: Gemini's best Flash model first, for the writing; a Flash-Lite model on
the same key when the first one's small free quota is spent; Groq last, which is quick
and free but tight on tokens a minute.

Each is asked for JSON that matches a schema. A model that is rate-limited, down,
slow, or returns something that is not the JSON asked for simply hands over to the next.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from jobfinder.core.config import settings

logger = logging.getLogger(__name__)


class ModelsUnavailable(RuntimeError):
    """No configured model could answer in time."""


@dataclass(frozen=True)
class Model:
    name: str
    base_url: str
    api_key: str
    provider: str  # "gemini" or "groq"


def models() -> list[Model]:
    out = []
    if settings.gemini_api_key:
        for name in (m.strip() for m in settings.tailor_models.split(",")):
            if name:
                out.append(Model(name, settings.gemini_base_url, settings.gemini_api_key, "gemini"))
    if settings.groq_api_key and settings.tailor_groq_model:
        out.append(Model(settings.tailor_groq_model, settings.groq_base_url,
                         settings.groq_api_key, "groq"))
    return out


def available() -> bool:
    return bool(models())


def _strict(schema: dict) -> dict:
    """The schema with `additionalProperties: false` on every object, which Groq's
    strict mode requires. Gemini is sent the plain schema."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_strict(v) for v in schema]
    return schema


def _parse(content: str) -> dict:
    content = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.S)
    if fenced:
        content = fenced.group(1)
    return json.loads(content)


def ask(system: str, user: str, schema: dict, *, name: str, max_tokens: int = 4000,
        deadline: float | None = None) -> tuple[dict, str]:
    """The first answer any model gives that parses as the JSON asked for.

    Returns (answer, model name). Raises ModelsUnavailable when every model failed or
    the deadline passed.
    """
    chain = models()
    if not chain:
        raise ModelsUnavailable("No model is configured for tailoring.")
    deadline = deadline or (time.monotonic() + settings.tailor_timeout_seconds)
    failures = []
    for model in chain:
        remaining = deadline - time.monotonic()
        if remaining < 4:
            break
        body = {
            "model": model.name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            # Enough thought to be careful with the wording, not so much that someone
            # waits half a minute for it.
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "strict": model.provider == "groq",
                    "schema": _strict(schema) if model.provider == "groq" else schema,
                },
            },
        }
        try:
            response = httpx.post(
                f"{model.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {model.api_key}"},
                json=body,
                timeout=httpx.Timeout(min(remaining, 40.0), connect=5.0),
            )
        except httpx.HTTPError as exc:
            failures.append(f"{model.name}: {type(exc).__name__}")
            continue
        if response.status_code >= 400:
            failures.append(f"{model.name}: HTTP {response.status_code}")
            logger.info("tailoring model %s refused: %s %s", model.name,
                        response.status_code, response.text[:300])
            continue
        try:
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                failures.append(f"{model.name}: answer cut off")
                continue
            return _parse(choice["message"]["content"] or ""), model.name
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            failures.append(f"{model.name}: unreadable answer ({type(exc).__name__})")
            continue
    logger.warning("no tailoring model answered: %s", "; ".join(failures))
    raise ModelsUnavailable("; ".join(failures) or "out of time")
