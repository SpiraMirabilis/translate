"""The response contract is code-owned, not prompt-owned.

Locks in the two halves of prompt_contract: stripping the pre-extraction
wording out of a stored prompt (without ever touching BOOK-SPECIFIC NOTES) and
rendering the authoritative section in its place.
"""
import os
import re

import pytest

from prompt_contract import (
    GENRE_EXAMPLES,
    LEGACY_PATTERNS,
    TEMPLATE_END,
    TEMPLATE_START,
    genre_example,
    has_legacy_contract,
    legacy_in_notes,
    response_contract_section,
    split_at_notes,
    strip_legacy_contract,
)

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A prompt in the shape every book froze at creation time.
LEGACY_PROMPT = """\
You are a Chinese-to-English literary translator.

---

CRITICAL RULES:

- Translate ALL content in full.
- All entity keys in the JSON output MUST be the original untranslated Chinese text. Never use English as an entity key.
- Output must be valid JSON matching the response template below exactly.
- You may add a "note" to a new entity in your JSON output ("entity": {"translation": "x", "note": "y"}). Add one whenever it helps.
- Keeping the note on an entity that already has one up to date — an age after a time skip — goes through the "note_updates" channel described further down.
- Use double quotes for dialogue.

---

ENTITY RULES:

Entity categories: characters, places.

The pre-translated entities block below is split per category into two sub-objects:
- "exact": entities whose source-language form appears literally in this chapter's source text. Use these translations exactly when they appear.
- "similar": entities NOT necessarily in this chapter. Each "similar" entry carries a "match" field ("prefix", "suffix", or "prefix+suffix") indicating which part overlapped.

In your response, do NOT use the "exact"/"similar" split — your response uses the flat per-category structure shown in the response template below.

Each entity entry must include:
- "translation": The translated English name.
- "last_chapter": Always set to the current chapter number.
- "gender": For characters only. Use "male", "female", or "neutral".
- "incorrect_translation": Only include if a previous translation was corrected. Note how it was corrected and use the correct form going forward.

If a category has no entities, include it as an empty object: "characters": {}

What counts as an entity:
- Personal names

---

PRE-TRANSLATED ENTITIES:

{{ENTITIES_JSON}}

---

// IMPORTANT: The response template below defines the JSON schema the program expects.
// Do not alter the structure. The ++++ markers are used by the program.

++++ Response Template Example

{
    "title": "Chapter 3 - The Great Apocalyptic Battle",
    "entities": {"characters": {}}
}
++++ Response Template End

---

BOOK-SPECIFIC NOTES:

- This is a Chinese xianxia novel.
- 妖 = "Yao" (capitalized), the sapient beast-race.
"""


# ── stripping ────────────────────────────────────────────────────────────────

def test_strip_removes_every_contract_section():
    out = strip_legacy_contract(LEGACY_PROMPT)
    assert not has_legacy_contract(out)
    for fragment in (
        "Output must be valid JSON",
        "All entity keys in the JSON output MUST be",
        "The pre-translated entities block below is split",
        "Each entity entry must include:",
        "If a category has no entities",
        'You may add a "note" to a new entity',
        "Keeping the note on an entity that already has one",
        TEMPLATE_START,
        TEMPLATE_END,
        "// IMPORTANT: The response template",
    ):
        assert fragment not in out, fragment


def test_strip_keeps_the_editable_prompt():
    out = strip_legacy_contract(LEGACY_PROMPT)
    for fragment in (
        "You are a Chinese-to-English literary translator.",
        "- Translate ALL content in full.",
        "- Use double quotes for dialogue.",
        "Entity categories: characters, places.",
        "What counts as an entity:",
        "PRE-TRANSLATED ENTITIES:",
        "{{ENTITIES_JSON}}",
    ):
        assert fragment in out, fragment


def test_strip_never_touches_book_specific_notes():
    """The author's half of the prompt is off limits — even when it repeats
    contract wording, which two live books do."""
    with_notes = LEGACY_PROMPT.replace(
        "- This is a Chinese xianxia novel.",
        '- This is a Chinese xianxia novel.\n'
        '- You may add a "note" to a new entity in your JSON output. Add one whenever it helps.')
    out = strip_legacy_contract(with_notes)
    assert out.split("BOOK-SPECIFIC NOTES:", 1)[1] == \
        with_notes.split("BOOK-SPECIFIC NOTES:", 1)[1]
    assert legacy_in_notes(out) == ["note_bullet"]


def test_strip_is_idempotent():
    once = strip_legacy_contract(LEGACY_PROMPT)
    assert strip_legacy_contract(once) == once


def test_strip_leaves_no_blank_or_separator_debris():
    out = strip_legacy_contract(LEGACY_PROMPT)
    assert "\n\n\n" not in out
    assert not re.search(r"^---[ \t]*\n\s*\n---", out, re.M)


def test_strip_tolerates_a_prompt_with_none_of_it():
    plain = "You are a translator.\n\nBOOK-SPECIFIC NOTES:\n\n- Be good.\n"
    assert strip_legacy_contract(plain) == plain
    assert strip_legacy_contract("") == ""
    assert strip_legacy_contract(None) is None


def test_split_at_notes_uses_the_last_header():
    head, tail = split_at_notes(LEGACY_PROMPT)
    assert tail.startswith("BOOK-SPECIFIC NOTES:")
    assert "BOOK-SPECIFIC NOTES:" not in head
    assert split_at_notes("no header here") == ("no header here", "")


def test_shipped_prompt_corpus_is_already_clean():
    """The corpus files must not reintroduce the contract — a new book freezes
    a copy of one, and that copy is what would go stale."""
    for name in os.listdir(os.path.join(SCRIPT_DIR, "prompts")):
        if not name.endswith(".txt"):
            continue
        text = open(os.path.join(SCRIPT_DIR, "prompts", name)).read()
        if "{{ENTITIES_JSON}}" not in text:
            continue                      # not a translation prompt
        assert not has_legacy_contract(text), name


# ── rendering ────────────────────────────────────────────────────────────────

TEMPLATE_JSON = '{\n    "title": "Chapter 3"\n}'


def test_full_mode_renders_prose_and_example():
    out = response_contract_section("full", ["characters"], TEMPLATE_JSON)
    assert out.startswith("RESPONSE FORMAT:")
    assert "Output must be valid JSON" in out
    assert "PRE-TRANSLATED ENTITIES block above" in out
    assert "Each entity entry must include:" in out
    assert TEMPLATE_START in out and TEMPLATE_END in out


def test_translate_only_drops_every_entity_rule():
    out = response_contract_section("translate_only", ["characters"], TEMPLATE_JSON)
    assert "Each entity entry must include" not in out
    assert '"exact"' not in out and '"similar"' not in out
    assert "entity key" not in out
    assert TEMPLATE_START in out


def test_entity_only_keeps_the_key_rule():
    out = response_contract_section("entity_only", ["characters"], TEMPLATE_JSON)
    assert "Every entity key MUST be the original untranslated text" in out


def test_gemini_gets_prose_without_the_worked_example():
    """Gemini's native responseSchema supersedes the example, and the two used
    to conflict."""
    out = response_contract_section("full", ["characters"], TEMPLATE_JSON,
                                    include_example=False)
    assert TEMPLATE_START not in out
    assert "Each entity entry must include:" in out


def test_gender_names_the_books_own_tracked_categories():
    out = response_contract_section("full", ["characters", "creatures"], TEMPLATE_JSON)
    assert '"gender": for entities in "characters", "creatures" only' in out


def test_gender_rule_is_omitted_when_nothing_is_tracked():
    out = response_contract_section("full", [], TEMPLATE_JSON)
    assert '"gender"' not in out
    assert '"translation": the translated English name.' in out


# ── genre examples ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["zh", "ja", "ko", "ru"])
def test_every_shipped_language_has_an_example(lang):
    ex = genre_example(lang)
    assert ex["title"] and ex["summary"] and ex["content"]
    assert ex is GENRE_EXAMPLES[lang]


def test_unknown_language_falls_back_to_chinese():
    assert genre_example("xx") is GENRE_EXAMPLES["zh"]
    assert genre_example(None) is GENRE_EXAMPLES["zh"]
    assert genre_example("zh-hans") is GENRE_EXAMPLES["zh"]


def test_every_legacy_pattern_is_named():
    assert len({name for name, _ in LEGACY_PATTERNS}) == len(LEGACY_PATTERNS)


# ── the footnote-candidate section ───────────────────────────────────────────
#
# It is appended by generate_system_prompt the same way ENTITY NOTES is, and
# must obey the same rule: never in translate_only, where the response carries
# no channel to return it on.

class _FootnoteConfig:
    entity_note_updates = True
    footnote_inline_scan = True


def _prompt(mode="full", footnote_section=None):
    from tests.conftest import FakeLogger
    from translation_engine import TranslationEngine

    class _Entities:
        def entities_inside_text(self, *a, **k):
            return {"exact": {}, "similar": {}}

    class _EM:
        entities = {}
        config = _FootnoteConfig()

        def entities_inside_text(self, *a, **k):
            return {"exact": {}, "similar": {}}

    eng = TranslationEngine(_FootnoteConfig(), FakeLogger(), _EM())
    return eng.generate_system_prompt(
        ["陈元看着刍狗。"], {"characters": {}}, do_count=False,
        book_prompt_template=strip_legacy_contract(LEGACY_PROMPT),
        chapter_number=7, mode=mode, gendered_categories=["characters"],
        footnote_section=footnote_section)


def test_footnote_section_is_absent_unless_supplied():
    out = _prompt()
    assert "FOOTNOTE CANDIDATES:" not in out
    assert "footnote_candidates" not in out


def test_footnote_section_is_appended_once_with_its_template_example():
    out = _prompt(footnote_section="FOOTNOTE CANDIDATES:\n\nTHE RULES")
    assert out.count("FOOTNOTE CANDIDATES:") == 1
    assert "THE RULES" in out
    # Supplying it also puts the channel in the worked example.
    assert '"footnote_candidates"' in out.split("RESPONSE FORMAT:")[1]


def test_footnote_section_never_reaches_pass_two():
    out = _prompt(mode="translate_only",
                  footnote_section="FOOTNOTE CANDIDATES:\n\nTHE RULES")
    assert "FOOTNOTE CANDIDATES:" not in out
    assert "footnote_candidates" not in out


def test_footnote_section_sits_between_entity_notes_and_the_contract():
    out = _prompt(footnote_section="FOOTNOTE CANDIDATES:\n\nTHE RULES")
    assert out.index("ENTITY NOTES:") < out.index("FOOTNOTE CANDIDATES:") \
        < out.index("RESPONSE FORMAT:")
