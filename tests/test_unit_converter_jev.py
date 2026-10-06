"""Jev false-positive filter in unit_converter.

Never hits the live API: ``jev_client.system_one`` and the LLM filter are
stubbed. conftest's ``no_live_jev`` drops TYPESAFE_KEY; tests that want Jev on
set a dummy key.
"""
import re

import pytest

import jev_client
import settings_store
import unit_converter
from unit_converter import convert_units

# The regex alone converts all four; the capitalised two are names.
LINES = [
    "Three Jin brothers came; the sword weighed three jin.",
    "The two Li sisters walked thirty li.",
]


@pytest.fixture
def jev_on(monkeypatch):
    monkeypatch.setenv("TYPESAFE_KEY", "test-key")
    store = {"jev_unit_filter": True, "jev_unit_confidence": 0.9}
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: store.get(k, d))
    return store


def _sentence(instructions):
    return instructions.rsplit("Sentence: ", 1)[1]


def stub_jev(monkeypatch, verdict, calls=None):
    """``verdict(sentence) -> (choice, confidence)`` answers every question."""
    def fake(state, questions, **kw):
        if calls is not None:
            calls.append(questions)
        out = {}
        for name, q in questions.items():
            choice, conf = verdict(_sentence(q["instructions"]))
            out[name] = {"choice": choice, "confidence": conf, "probabilities": {}}
        return out
    monkeypatch.setattr(jev_client, "system_one", fake)


def name_or_place(sentence):
    """Capitalised Jin/Li inside the markers is a name; lowercase is a unit."""
    word = re.search(r">>>(.*?)<<<", sentence).group(1)
    return ("not_unit", 0.99) if re.search(r"\b(Jin|Li)\b", word) else ("unit", 0.99)


def test_jev_drops_confident_false_positives(monkeypatch, jev_on):
    stub_jev(monkeypatch, name_or_place)
    llm = []
    monkeypatch.setattr(unit_converter, "_filter_false_positives",
                        lambda ctx, model, **kw: llm.append(ctx) or set())
    out = convert_units(LINES, cleaning_model="claude:haiku")
    assert out == ["Three Jin brothers came; the sword weighed three jin (1.5 kg).",
                   "The two Li sisters walked thirty li (15 km)."]
    assert llm == []  # Jev decided everything; the LLM was never called


def test_unsure_matches_escalate_to_cleaning_model(monkeypatch, jev_on):
    # Jev is unsure about everything: all of it goes to the LLM, which flags
    # one match; the rest are converted.
    stub_jev(monkeypatch, lambda s: ("not_unit", 0.5))
    seen = {}

    def llm(ctx, model, **kw):
        seen.update(ctx)
        return {int(k) for k, s in ctx.items() if re.search(r"(Jin|Li)<<<", s)}
    monkeypatch.setattr(unit_converter, "_filter_false_positives", llm)
    out = convert_units(LINES, cleaning_model="claude:haiku")
    assert len(seen) == 4  # everything escalated
    assert out[0] == "Three Jin brothers came; the sword weighed three jin (1.5 kg)."


def test_only_unsure_matches_reach_the_llm(monkeypatch, jev_on):
    def verdict(s):
        return ("unit", 0.5) if "jin<<<" in s else name_or_place(s)
    stub_jev(monkeypatch, verdict)
    seen = []
    monkeypatch.setattr(unit_converter, "_filter_false_positives",
                        lambda ctx, model, **kw: seen.append(dict(ctx)) or set())
    convert_units(LINES, cleaning_model="claude:haiku")
    assert len(seen) == 1 and list(seen[0].values()) == [
        "the sword weighed >>>three jin<<<."]


def test_unsure_without_cleaning_model_converts(monkeypatch, jev_on):
    stub_jev(monkeypatch, lambda s: ("not_unit", 0.5))
    out = convert_units(LINES)
    assert "three jin (" in out[0]


def test_jev_failure_falls_back_to_cleaning_model(monkeypatch, jev_on):
    def boom(state, questions, **kw):
        raise jev_client.JevError("HTTP 401: bad key")
    monkeypatch.setattr(jev_client, "system_one", boom)
    seen = []
    monkeypatch.setattr(unit_converter, "_filter_false_positives",
                        lambda ctx, model, **kw: seen.append(ctx) or set())
    out = convert_units(LINES, cleaning_model="claude:haiku")
    assert len(seen) == 1 and len(seen[0]) == 4  # every match went to the LLM
    assert "three jin (" in out[0]


def test_jev_failure_without_cleaning_model_converts_all(monkeypatch, jev_on):
    def boom(state, questions, **kw):
        raise jev_client.JevError("timeout")
    monkeypatch.setattr(jev_client, "system_one", boom)
    assert convert_units(LINES) == convert_units(LINES, use_jev=False)


def test_setting_off_skips_jev(monkeypatch, jev_on):
    jev_on["jev_unit_filter"] = False
    calls = []
    stub_jev(monkeypatch, name_or_place, calls)
    convert_units(LINES)
    assert calls == []


def test_no_key_skips_jev_even_when_forced(monkeypatch):
    calls = []
    stub_jev(monkeypatch, name_or_place, calls)
    convert_units(LINES, use_jev=True)
    assert calls == []


def test_use_jev_false_overrides_setting(monkeypatch, jev_on):
    calls = []
    stub_jev(monkeypatch, name_or_place, calls)
    convert_units(LINES, use_jev=False)
    assert calls == []


def test_threshold_from_settings(monkeypatch, jev_on):
    jev_on["jev_unit_confidence"] = 0.995
    stub_jev(monkeypatch, name_or_place)  # 0.99 < 0.995: all unsure
    assert convert_units(LINES) == convert_units(LINES, use_jev=False)


def test_batches_into_one_request_per_chunk(monkeypatch, jev_on):
    monkeypatch.setattr(unit_converter, "JEV_UNIT_BATCH", 2)
    calls = []
    stub_jev(monkeypatch, name_or_place, calls)
    convert_units(LINES)
    assert [len(q) for q in calls] == [2, 2]


def test_lowercase_time_units_skip_jev(monkeypatch, jev_on):
    calls = []
    stub_jev(monkeypatch, lambda s: ("not_unit", 0.99), calls)
    out = convert_units(["He waited two shichen."])
    assert calls == []
    assert "shichen" not in out[0]


def test_highlight_lands_on_matches_after_the_first_sentence():
    from unit_converter import _extract_sentence_context
    line = "He sighed. The sword weighed three jin."
    start = line.index("three jin")
    end = start + len("three jin")
    ctx, off = _extract_sentence_context(line, start, end)
    assert ctx[start - off:end - off] == "three jin"
