"""Client for TypeSafe's System One API (the Jev classification model).

Jev does not generate prose: each request carries a ``state`` (the content to
judge) and a map of typed questions, and the answer to each is a decision --
a ``choice`` with per-option probabilities and a ``confidence``, a ``score``,
or a ``noul`` (yes/no) probability. It is cheap ($0.042/M input tokens, output
free) and fast, which makes it the tool for classification inside the app,
never for translation.

Deliberately NOT a ``providers.ModelProvider``: the provider factory, the
Settings provider cards' ``/test`` (a chat completion) and every model picker
assume a chat model.

Deliberately NOT the official ``typesafe-sdk``: it has no apt package, and its
httpcore2 dependency needs h11>=0.16, which pip would install into ~/.local
over the apt h11 0.14 that uvicorn runs on. The API is one POST.

Config: ``TYPESAFE_KEY`` (.env, secret), ``JEV_MODEL`` (settings.json).

Two backends, picked by the model spec (``JEV_MODEL`` or a caller's ``model=``):

- ``jev-latest`` (or any bare name) -- TypeSafe System One, ``TYPESAFE_KEY``.
- ``openai:decisions`` -- OpenAI's Decisions API (``POST /v1/decisions``,
  ``gpt-6-luna``, $0.10/M input, output free), ``OPENAI_KEY``.
  ``openai:<model>`` names a specific Decisions model.

Callers see Jev's shapes either way: ``questions`` is Jev's
``{name: {"type", "instructions", "criteria"}}`` map, and the answers come back
as ``{name: {"choice", "confidence", "probabilities": {option: p}}}``. The
OpenAI backend translates both directions (questions/answers are arrays there,
``criteria`` is a ``choices`` array, ``state`` is a string ``input``). Only
``choice`` questions are translated -- nothing here asks anything else.
"""
import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_KEY"

OPENAI_API_URL = "https://api.openai.com/v1/decisions"
OPENAI_DEFAULT_MODEL = "gpt-6-luna"
OPENAI_KEY_ENV = "OPENAI_KEY"
_OPENAI_PREFIXES = ("openai:", "oai:")

# 429 (rate limited) and 529 (overloaded) are transient; everything else is not.
# OpenAI also reports transient trouble as 500/502/503.
_RETRY_STATUSES = (429, 529)
_OPENAI_RETRY_STATUSES = (429, 500, 502, 503, 529)
MAX_RETRIES = 3
MAX_RETRY_WAIT_SECONDS = 10.0


class JevError(Exception):
    """A System One request failed (missing key, HTTP error, bad response)."""


def _model() -> str:
    return os.getenv("JEV_MODEL", "").strip() or DEFAULT_MODEL


def resolve(model: str = None):
    """``(backend, model_name)`` for a spec; ``None`` means ``JEV_MODEL``.

    ``openai:decisions`` -> ``("openai", "gpt-6-luna")``,
    ``openai:<name>`` -> ``("openai", name)``, anything else -> ``("typesafe", spec)``.
    """
    spec = (model or "").strip() or _model()
    low = spec.lower()
    for prefix in _OPENAI_PREFIXES:
        if low.startswith(prefix):
            name = spec[len(prefix):].strip()
            if not name or name.lower() == "decisions":
                name = OPENAI_DEFAULT_MODEL
            return "openai", name
    return "typesafe", spec


def key_env(model: str = None) -> str:
    """The env var holding the key the spec's backend needs."""
    return OPENAI_KEY_ENV if resolve(model)[0] == "openai" else KEY_ENV


def is_configured(model: str = None) -> bool:
    return bool(os.getenv(key_env(model), "").strip())


def label(model: str = None) -> str:
    """How user-facing messages name the classifier, e.g. "Probability model (Jev)"."""
    backend = "OpenAI Decisions" if resolve(model)[0] == "openai" else "Jev"
    return f"Probability model ({backend})"


def short_label(model: str = None) -> str:
    """Compact tag for space-limited fields (automod reasons): "jev" / "decisions"."""
    return "decisions" if resolve(model)[0] == "openai" else "jev"


def _retry_wait(resp, attempt: int) -> float:
    raw = resp.headers.get("retry-after") if resp is not None else None
    try:
        wait = float(raw) if raw is not None else 2.0 ** attempt
    except ValueError:
        wait = 2.0 ** attempt
    return max(0.0, min(wait, MAX_RETRY_WAIT_SECONDS))


def _post(url, body, headers, timeout, retry_statuses, label):
    """POST with retries on transient statuses; returns the decoded JSON."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise JevError(f"request failed: {e}") from e

        if resp.status_code in retry_statuses and attempt < MAX_RETRIES:
            wait = _retry_wait(resp, attempt)
            logger.info(f"{label} HTTP {resp.status_code}; retrying in {wait:.1f}s")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            raise JevError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            return resp.json()
        except ValueError as e:
            raise JevError(f"response is not JSON: {resp.text[:200]}") from e

    raise JevError("retries exhausted")  # unreachable: the last attempt returns or raises


def system_one(state, questions: dict, *, model: str = None, timeout=(5, 30)) -> dict:
    """POST one System One request and return its ``answers`` map.

    ``questions`` is ``{name: {"type": ..., "instructions": ..., "criteria": ...}}``;
    the answers come back under the same names. ``model`` is a spec (see
    ``resolve``); an ``openai:`` spec goes to OpenAI's Decisions API, translated
    to and from these shapes. Raises ``JevError``.
    """
    backend, name = resolve(model)
    if backend == "openai":
        return _openai_decisions(state, questions, name, timeout)

    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        raise JevError(f"{KEY_ENV} is not set")

    body = {"model": name, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = _post(API_URL, body, headers, timeout, _RETRY_STATUSES, "Jev")

    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise JevError(f"response has no answers: {str(data)[:200]}")
    logger.debug(f"Jev {data.get('model')} usage: {data.get('usage')}")
    return answers


# ── OpenAI Decisions backend ────────────────────────────────────────

def _openai_question(name: str, q: dict) -> dict:
    qtype = q.get("type")
    if qtype != "choice":
        raise JevError(f"question type {qtype!r} is not supported on the OpenAI backend")
    criteria = q.get("criteria") or {}
    return {
        "type": "choice",
        "name": name,
        "instructions": q.get("instructions", ""),
        "choices": [{"value": k, "description": v} for k, v in criteria.items()],
    }


def _openai_answer(ans: dict) -> dict:
    """One Decisions answer in Jev's shape: probabilities as a map.

    A refusal (or anything else unexpected) passes through without a
    ``choice``, which ``choice()`` rejects and the batch callers count as unsure.
    """
    out = {k: v for k, v in ans.items() if k not in ("name", "probabilities")}
    probs = ans.get("probabilities")
    if isinstance(probs, list):
        out["probabilities"] = {
            p.get("value"): p.get("probability")
            for p in probs if isinstance(p, dict) and "value" in p
        }
    elif isinstance(probs, dict):
        out["probabilities"] = probs
    return out


def _openai_decisions(state, questions: dict, model: str, timeout) -> dict:
    key = os.getenv(OPENAI_KEY_ENV, "").strip()
    if not key:
        raise JevError(f"{OPENAI_KEY_ENV} is not set")

    # Decisions takes a string (or message array) as input; Jev's structured
    # states go over as JSON so field names still frame the content.
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    body = {
        "model": model,
        "input": text,
        "questions": [_openai_question(n, q) for n, q in questions.items()],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = _post(OPENAI_API_URL, body, headers, timeout, _OPENAI_RETRY_STATUSES,
                 "OpenAI Decisions")

    raw = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise JevError(f"response has no answers: {str(data)[:200]}")
    answers = {
        a["name"]: _openai_answer(a)
        for a in raw if isinstance(a, dict) and a.get("name") is not None
    }
    logger.debug(f"OpenAI Decisions {data.get('model')} usage: {data.get('usage')}")
    return answers


def choice(state, instructions: str, criteria: dict, **kw) -> dict:
    """Ask one choice question.

    Returns ``{"choice", "confidence", "probabilities"}``. ``criteria`` maps
    each option to a description of when it applies.
    """
    answers = system_one(
        state,
        {"q": {"type": "choice", "instructions": instructions, "criteria": criteria}},
        **kw,
    )
    ans = answers.get("q")
    if not isinstance(ans, dict) or ans.get("choice") not in criteria:
        raise JevError(f"unexpected answer: {ans!r}")
    try:
        confidence = float(ans.get("confidence"))
    except (TypeError, ValueError):
        raise JevError(f"answer has no confidence: {ans!r}")
    return {
        "choice": ans["choice"],
        "confidence": confidence,
        "probabilities": ans.get("probabilities") or {},
    }
