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
"""
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_KEY"

# 429 (rate limited) and 529 (overloaded) are transient; everything else is not.
_RETRY_STATUSES = (429, 529)
MAX_RETRIES = 3
MAX_RETRY_WAIT_SECONDS = 10.0


class JevError(Exception):
    """A System One request failed (missing key, HTTP error, bad response)."""


def is_configured() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip())


def _model() -> str:
    return os.getenv("JEV_MODEL", "").strip() or DEFAULT_MODEL


def _retry_wait(resp, attempt: int) -> float:
    raw = resp.headers.get("retry-after") if resp is not None else None
    try:
        wait = float(raw) if raw is not None else 2.0 ** attempt
    except ValueError:
        wait = 2.0 ** attempt
    return max(0.0, min(wait, MAX_RETRY_WAIT_SECONDS))


def system_one(state, questions: dict, *, model: str = None, timeout=(5, 30)) -> dict:
    """POST one System One request and return its ``answers`` map.

    ``questions`` is ``{name: {"type": ..., "instructions": ..., "criteria": ...}}``;
    the answers come back under the same names. Raises ``JevError``.
    """
    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        raise JevError(f"{KEY_ENV} is not set")

    body = {"model": model or _model(), "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(API_URL, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise JevError(f"request failed: {e}") from e

        if resp.status_code in _RETRY_STATUSES and attempt < MAX_RETRIES:
            wait = _retry_wait(resp, attempt)
            logger.info(f"Jev HTTP {resp.status_code}; retrying in {wait:.1f}s")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            raise JevError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        try:
            data = resp.json()
        except ValueError as e:
            raise JevError(f"response is not JSON: {resp.text[:200]}") from e

        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            raise JevError(f"response has no answers: {str(data)[:200]}")
        logger.debug(f"Jev {data.get('model')} usage: {data.get('usage')}")
        return answers

    raise JevError("retries exhausted")  # unreachable: the last attempt returns or raises


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
