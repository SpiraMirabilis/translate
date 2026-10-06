"""Jev triage for chapter conflicts.

A conflict is an incoming queue item whose chapter number is already stored
with different source text. The commonest benign cause is that one side is
nothing but an author's note or an advertisement: a leave notice, a vote plea,
promo for the author's next book. Jev classifies each side as ``story`` or
``filler``, and when one side is confidently each the conflict resolves itself:

    existing filler + incoming story  ->  "proceed" (overwrite existing)
    existing story  + incoming filler ->  "cancel"  (skip the queue item)
    anything else                     ->  None      (ask the user)

Every result is returned (and logged by the caller) whether or not it is acted
on, so the threshold can be calibrated against real conflicts.
"""
import logging

import jev_client
import settings_store

logger = logging.getLogger(__name__)

MODES = ("off", "suggest", "auto")
DEFAULT_THRESHOLD = 0.9

# The state cap is 32k tokens (state + longest question). CJK runs about a
# token per character, so two sides never get near it at this length -- and a
# real chapter cut short is still unmistakably story.
MAX_STATE_CHARS = 12000

INSTRUCTIONS = (
    "This is one chapter of a serialized web novel, as posted by the author. "
    "Does it contain any story narrative, or is it solely non-story material? "
    "A chapter with story narrative plus an author's note before or after it "
    "counts as story."
)
CRITERIA = {
    "story": (
        "Contains narrative prose of the novel: scenes, dialogue, or events "
        "of the story, even if an author's note is attached."
    ),
    "filler": (
        "Solely non-story material: an author's note, leave or update-schedule "
        "notice, request for votes, tickets or subscriptions, thanks list, "
        "advertisement or promotion for another book, app or site, or a "
        "placeholder / 'chapter coming soon' notice. No story narrative."
    ),
}


def mode() -> str:
    m = str(settings_store.get("jev_chapter_conflict", "auto") or "").strip().lower()
    return m if m in MODES else "auto"


def threshold() -> float:
    try:
        t = float(settings_store.get("jev_conflict_confidence", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return t if 0.0 < t <= 1.0 else DEFAULT_THRESHOLD


def enabled() -> bool:
    return mode() != "off" and jev_client.is_configured()


def _state(title, lines):
    if isinstance(lines, str):
        lines = lines.splitlines()
    text = "\n".join(str(l).strip() for l in (lines or []) if str(l).strip())
    if len(text) > MAX_STATE_CHARS:
        text = text[:MAX_STATE_CHARS] + "\n[…truncated]"
    return {"title": str(title or ""), "text": text}


def classify_chapter(title, lines) -> dict:
    """``{"choice": "story"|"filler", "confidence", "probabilities"}``; raises JevError."""
    return jev_client.choice(_state(title, lines), INSTRUCTIONS, CRITERIA)


def decide(existing: dict, incoming: dict, threshold: float):
    """Map the two verdicts onto a conflict decision, or None to ask the user."""
    def confident(side, label):
        return side.get("choice") == label and side.get("confidence", 0.0) >= threshold

    if confident(existing, "filler") and confident(incoming, "story"):
        return "proceed"
    if confident(existing, "story") and confident(incoming, "filler"):
        return "cancel"
    return None


def suggest(existing: dict, incoming: dict):
    """The decision the verdicts point at, ignoring confidence (for the modal hint)."""
    return decide(existing, incoming, 0.0)


def triage(existing_title, existing_lines, new_title, new_lines, threshold_=None) -> dict:
    """Classify both sides. Never raises: a failure comes back as ``{"error": ...}``."""
    t = threshold() if threshold_ is None else threshold_
    try:
        existing = classify_chapter(existing_title, existing_lines)
        incoming = classify_chapter(new_title, new_lines)
    except jev_client.JevError as e:
        return {"error": str(e), "threshold": t}
    except Exception as e:  # never let triage take a translation job down
        logger.exception("Jev triage failed")
        return {"error": f"{type(e).__name__}: {e}", "threshold": t}
    return {
        "existing": existing,
        "incoming": incoming,
        "threshold": t,
        "decision": decide(existing, incoming, t),
        "suggestion": suggest(existing, incoming),
    }
