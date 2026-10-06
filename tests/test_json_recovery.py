"""Automatic recovery of a chunk response that is not valid JSON.

A survey of api_calls (2026-08-01 to 2026-09-22) found 1% of chunk responses
unparseable, in two populations: streams that ended early, and responses that
are complete but carry an unescaped quote or a stray one. The first kind is
never repaired -- closing the brackets would save a fraction of the chapter as
if it were whole -- and goes straight to a retry. The second is repaired with
json_repair, and the repair is used only when it is provably lossless: every
word survives, the line count matches the raw array's separators, and no line
is JSON residue. Everything else is a retry, and the JSON Fix modal is reached
only once the retry budget is spent.

Covered here: classification, each observed breakage shape, the gates that
reject a damaged repair, the library being absent, and the engine helper's
three outcomes.
"""
import json

import pytest

from tests.conftest import FakeLogger
import json_recovery as jr


def _doc(lines, **extra):
    d = {"title": "t", "content": lines, "summary": "s", "chapter": 1}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


# -- classify ------------------------------------------------------------------

def test_valid_json_is_complete():
    assert jr.classify(_doc(["a", "b"])) == jr.COMPLETE


def test_unescaped_inner_quote_is_still_complete():
    """Broken, but every bracket is closed: a candidate for repair, not a retry."""
    assert jr.classify('{"content": ["He said "hi" and left"]}') == jr.COMPLETE


def test_truncated_mid_string():
    assert jr.classify('{"content": ["a", "b') == jr.TRUNCATED


def test_truncated_mid_array():
    assert jr.classify('{"content": ["a", "b",') == jr.TRUNCATED


def test_truncated_after_content_array():
    """The prose is whole but the entities never arrived: still a retry."""
    assert jr.classify('{"content": ["a", "b"], "entities": {') == jr.TRUNCATED


def test_escaped_quote_does_not_close_a_string():
    assert jr.classify('{"content": ["say \\"hi\\"", "b"]}') == jr.COMPLETE
    assert jr.classify('{"content": ["say \\"hi\\", b') == jr.TRUNCATED


def test_brackets_inside_strings_are_ignored():
    assert jr.classify('{"content": ["a [b] {c}", "d"]}') == jr.COMPLETE
    assert jr.classify('{"content": ["a ]]] }}}"]}') == jr.COMPLETE


def test_fences_are_stripped_before_classifying():
    assert jr.classify("```json\n" + _doc(["a"]) + "\n```") == jr.COMPLETE


# -- repairs that are accepted --------------------------------------------------

def test_unescaped_inner_quote_is_repaired():
    raw = '{"title":"t","content":["He said "wait" and left","x"],"summary":"s"}'
    d, reason = jr.try_repair(raw)
    assert reason == "faithful"
    assert d["content"] == ['He said "wait" and left', "x"]
    assert d["summary"] == "s"


def test_pretty_printed_with_blank_lines():
    raw = '{\n  "title": "t",\n  "content": [\n    "He said "wait" and left",\n    "",\n    "x"\n  ],\n  "summary": "s"\n}'
    d, reason = jr.try_repair(raw)
    assert reason == "faithful"
    assert d["content"] == ['He said "wait" and left', "", "x"]


def test_cjk_brackets_around_an_inner_quote():
    raw = '{"title":"t","content":["【Who "did" it】","x"],"summary":"s"}'
    d, reason = jr.try_repair(raw)
    assert reason == "faithful"
    assert d["content"][0] == '【Who "did" it】'


def test_fenced_response_is_repaired():
    raw = "```json\n" + '{"title":"t","content":["He said "wait"","x"],"summary":"s"}' + "\n```"
    d, reason = jr.try_repair(raw)
    assert reason == "faithful"
    assert d["content"] == ['He said "wait"', "x"]


# -- repairs that are rejected --------------------------------------------------

def test_stray_quote_before_bracket_is_rejected():
    """json_repair turns `],"entities":{` into two more lines; text survives, structure doesn't."""
    raw = '{"title":"t","content":["a","b","],"entities":{"characters":{"x":"y"}},"summary":"s"}'
    d, reason = jr.try_repair(raw)
    assert d is None and reason.startswith("structure")


def test_inner_quote_around_a_comma_is_rejected():
    """The line gets split into pieces: every word survives, the line count doesn't."""
    raw = '{"title":"t","content":["He said "wait, now" and left","x"],"summary":"s"}'
    d, reason = jr.try_repair(raw)
    assert d is None and reason.startswith("structure")


def _stub(monkeypatch, result=None, exc=None):
    class Lib:
        @staticmethod
        def loads(_):
            if exc:
                raise exc
            return result
    monkeypatch.setattr(jr, "_load_json_repair", lambda: Lib())


def test_lossy_repair_is_rejected(monkeypatch):
    raw = _doc(["one", "two three", "four"])
    _stub(monkeypatch, {"title": "t", "content": ["one", "two", "four"], "summary": "s", "chapter": 1})
    d, reason = jr.try_repair(raw)
    assert d is None and reason.startswith("lossy")


@pytest.mark.parametrize("shape", [
    ["not", "a", "dict"],
    {"title": "t"},                                    # no content
    {"title": "t", "content": "a string"},
    {"title": "t", "content": [{"line": "a"}]},        # not strings
    {"title": "t", "content": ["a"], 'entities":{"x': 1},  # a key that is not a key
])
def test_wrong_shape_is_rejected(monkeypatch, shape):
    _stub(monkeypatch, shape)
    d, reason = jr.try_repair('{"content": ["a"')
    assert d is None and reason == "wrong_shape"


def test_library_absent(monkeypatch):
    monkeypatch.setattr(jr, "_load_json_repair", lambda: None)
    assert jr.try_repair('{"content": ["a" "b"]}') == (None, "library_missing")


def test_library_exception_never_propagates(monkeypatch):
    _stub(monkeypatch, exc=RuntimeError("boom"))
    d, reason = jr.try_repair('{"content": ["a" "b"]}')
    assert d is None and reason.startswith("repair_raised")


def test_describe_error():
    assert jr.describe_error(_doc(["a"])) == "parses"
    assert jr.describe_error('{"a":["x"y"]}').startswith("Expecting ',' delimiter at ")


# -- the engine helper ----------------------------------------------------------

class _Config:
    translation_model = "test:model"


def _engine(**over):
    from translation_engine import TranslationEngine
    cfg = _Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return TranslationEngine(cfg, FakeLogger(), entity_manager=None)


def _run(engine, text, attempt, max_retries=2):
    events = []
    outcome = engine._recover_unparseable_chunk(text, attempt, max_retries, 1, 1, events.append)
    return outcome, [e["phase"] for e in events]


def test_switch_defaults_on():
    assert _engine().json_auto_repair is True
    assert _engine(json_auto_repair=False).json_auto_repair is False


def test_setting_is_in_the_store_schema():
    import settings_store
    assert settings_store.SCHEMA["json_auto_repair"] == ("JSON_AUTO_REPAIR", True, bool)


def test_truncated_retries_immediately():
    (outcome, parsed), phases = _run(_engine(), '{"content": ["a", "b', attempt=0)
    assert (outcome, parsed) == ("retry", None)
    assert phases == ["json_truncated"]


def test_truncated_on_last_attempt_gives_up():
    (outcome, _), _ = _run(_engine(), '{"content": ["a", "b', attempt=2)
    assert outcome == "give_up"


def test_truncated_is_never_repaired_even_with_the_switch_on():
    """Closing the brackets would parse; the helper must not try."""
    (outcome, parsed), phases = _run(_engine(json_auto_repair=True), '{"title":"t","content":["a","b"', attempt=0)
    assert outcome == "retry" and parsed is None
    assert "json_repaired" not in phases


def test_repairable_response_is_parsed():
    raw = '{"title":"t","content":["He said "wait" and left","x"],"summary":"s"}'
    (outcome, parsed), phases = _run(_engine(), raw, attempt=0)
    assert outcome == "parsed"
    assert parsed["content"] == ['He said "wait" and left', "x"]
    assert phases == ["json_repaired"]


def test_rejected_repair_retries():
    raw = '{"title":"t","content":["He said "wait, now" and left","x"],"summary":"s"}'
    (outcome, _), phases = _run(_engine(), raw, attempt=1)
    assert outcome == "retry"
    assert phases == ["json_repair_rejected"]


def test_switch_off_skips_repair():
    raw = '{"title":"t","content":["He said "wait" and left","x"],"summary":"s"}'
    (outcome, parsed), phases = _run(_engine(json_auto_repair=False), raw, attempt=0)
    assert (outcome, parsed) == ("retry", None)
    assert phases == []


def test_no_progress_callback_is_fine():
    engine = _engine()
    assert engine._recover_unparseable_chunk('{"content": ["a"', 0, 2, 1, 1) == ("retry", None)
