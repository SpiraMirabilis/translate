"""The streamed-output repetition guard: disarmed by default, and blind to prose.

The guard was built for a DeepSeek generation that looped on a phrase until it
hit the output cap. It aborts the stream mid-JSON, so a false positive reaches
the user as a malformed response truncated at an arbitrary point -- and since it
only ever aborts, never repairs, a false positive burns the whole retry budget
and fails the chapter anyway.

It false-fired on book 99, on every model tried: those raws close a chapter with
the author's afterword behind a 17-dash horizontal rule, the translator
reproduces the rule faithfully, and `-` x17 read as a token loop.

The old threshold of 10 was below ordinary prose in this corpus. Accepted
translations carry ellipsis scene-breaks 30 characters wide and screams like
"Kyaaa...ack" (30 a's) and "Bzzz...z" (20 z's) -- book 42 is full of them. A
real loop runs to the output cap, so the bar is set an order of magnitude above
onomatopoeia instead.

Covered here: the default is off, the toggle arms it, layout and onomatopoeia
are not loops, and a genuine runaway is still caught when it is armed.
"""
import pytest

from tests.conftest import FakeLogger


class _Config:
    """Minimal config for the guard. Deliberately carries no repetition_guard."""
    translation_model = "test:model"


def _engine(**over):
    from translation_engine import TranslationEngine

    cfg = _Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return TranslationEngine(cfg, FakeLogger(), entity_manager=None)


# -- the guard is off unless asked for ---------------------------------------

def test_disarmed_by_default():
    """A config that never heard of the setting leaves the guard off."""
    assert _engine().repetition_guard is False


def test_disarmed_when_explicitly_off():
    assert _engine(repetition_guard=False).repetition_guard is False


def test_armed_by_the_setting():
    assert _engine(repetition_guard=True).repetition_guard is True


def test_config_default_is_off():
    """The shipped default in config.py is off, not just the getattr fallback."""
    import os
    import importlib
    import config as config_module

    os.environ.pop("REPETITION_GUARD", None)
    importlib.reload(config_module)
    assert config_module.TranslationConfig().repetition_guard is False


def test_setting_is_in_the_store_schema():
    """Without a SCHEMA row the Settings UI cannot persist the toggle."""
    import settings_store

    assert settings_store.SCHEMA["repetition_guard"] == (
        "REPETITION_GUARD", False, bool)


# -- layout is not degeneration ----------------------------------------------

# The exact shape book 99's raws put before the author's afterword.
BOOK99_TAIL = (
    '"Zhao Tieying snatched up the cleaver by the stove.", "", "\u2026\u2026", "", '
    '"-----------------", "", "PS: Begging for monthly tickets!"'
)


def test_book99_horizontal_rule_is_not_a_loop():
    assert _engine()._detect_repetition(BOOK99_TAIL) is False


@pytest.mark.parametrize("ch", list("-=_*~.\u00b7\u2022\u2219\u2026\u2014\u2015#+<>/\\|")
                               + ["\u2500", "\u2501", "\u2550", "\u25a0"])
def test_layout_runs_are_never_loops(ch):
    """A rule or scene break, drawn 80 wide -- length must not matter at all."""
    assert _engine()._detect_repetition("prose before the break\n" + ch * 80) is False


def test_ellipsis_scene_break_is_not_a_loop():
    """Book 30 ch273 closes a scene with a 30-wide ellipsis line."""
    assert _engine()._detect_repetition('"everything and anything\u2026", "", "'
                                        + "\u2026" * 30 + '", ""') is False


# -- onomatopoeia is not degeneration ----------------------------------------

@pytest.mark.parametrize("scream", [
    "Kya" + "a" * 30 + "ck\u2015\u2015\u2015!!!!",   # book 42 ch51
    "KRA" + "A" * 19 + "SH!!!!",                      # book 42 ch564
    "Bz" + "z" * 20 + "\u2014\u2014!!!!",              # book 42 ch689
    "Awo" + "o" * 14 + "\u2501\u2501!!",               # book 42 ch563
])
def test_screams_are_not_loops(scream):
    assert _engine()._detect_repetition('"' + scream + '", ""') is False


def test_run_just_under_the_bar_is_allowed():
    assert _engine()._detect_repetition("she screamed a" + "a" * 38) is False


# -- a real runaway is still caught when armed -------------------------------

def test_run_at_the_bar_is_caught():
    assert _engine()._detect_repetition("she screamed a" + "a" * 39) is True


def test_latin_runaway_still_caught():
    assert _engine()._detect_repetition("the boy said " + "a" * 150) is True


def test_single_cjk_glyph_runaway_still_caught():
    """The original case: a CJK glyph emitted until the cap."""
    assert _engine()._detect_repetition("some prose then " + "\u6846" * 60) is True


def test_cjk_phrase_loop_still_caught():
    assert _engine()._detect_repetition("some prose then "
                                        + "\u6539\u9769\u5f00\u653e" * 8) is True


def test_layout_run_does_not_mask_a_loop_behind_it():
    """A rule early in the tail must not shadow a genuine loop after it.

    This is why the scan is finditer and not search: the first match used to
    decide the answer, so any output that drew a rule went unchecked for the
    rest of the window.
    """
    tail = "-" * 20 + " and then " + "q" * 60
    assert _engine()._detect_repetition(tail) is True


def test_only_the_tail_is_examined():
    """A loop that scrolled out of the 200-char window is no longer seen.

    The guard is a tail check: it catches a loop while it is still being
    emitted, not after the model recovered from one.
    """
    scrolled = "q" * 60 + " the kitchen went quiet and nobody moved again. " * 12
    assert len(scrolled) - 200 > 60  # the run really is out of the window
    assert _engine()._detect_repetition(scrolled) is False
