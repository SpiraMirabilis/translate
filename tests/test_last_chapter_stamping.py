"""``last_chapter`` is stamped by code, never asked of the model.

The contract used to require it on every entity of every response ("always set
to the current chapter number") — a field the model could only echo back from
what the prompt already told it, billed as output tokens once per entity per
chunk, and wrong whenever it copied the worked example's number instead. It is
now written from the chapter number the run settled on, and a value a model
volunteers anyway is overwritten rather than trusted.

Locked here on both sides of the exchange: the prompt no longer mentions or
shows the field, and every path that persists an entity stamps it.
"""
import json

import pytest

from tests.conftest import FakeLogger

import prompt_contract as pc
from prompt_contract import response_contract_section


class _Config:
    entity_note_updates = True
    footnote_inline_scan = True
    translation_model = "test:model"


def _engine(entity_manager=None):
    from translation_engine import TranslationEngine
    return TranslationEngine(_Config(), FakeLogger(), entity_manager=entity_manager)


# ── the prompt never asks for it ─────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["full", "entity_only", "translate_only"])
def test_contract_section_never_mentions_the_field(mode):
    out = response_contract_section(mode, ["characters"], '{"title": "x"}')
    assert "last_chapter" not in out
    # The rest of the field list is untouched.
    if mode != "translate_only":
        assert '"translation": the translated English name.' in out


def test_no_shipped_genre_example_carries_it():
    for lang, example in pc.GENRE_EXAMPLES.items():
        for category, entities in example["entities"].items():
            for key, entry in entities.items():
                assert "last_chapter" not in entry, f"{lang}/{category}/{key}"


def test_response_template_omits_it_for_real_and_placeholder_entities():
    tmpl = json.loads(_engine()._build_response_template(
        ["characters", "places"],
        {"characters": {"钟岳": {"translation": "Zhong Yue", "gender": "male"}}},
        chapter_number=7, gendered_categories=["characters"]))
    assert "last_chapter" not in json.dumps(tmpl)
    # The fields that are still contracted survive.
    assert tmpl["entities"]["characters"]["钟岳"]["gender"] == "male"
    assert tmpl["entities"]["places"]          # placeholder still rendered


def test_a_legacy_base_template_does_not_reintroduce_it():
    """A book restored from an old backup still carries the field in its frozen
    ``++++`` block; re-using those example entries must drop it."""
    base = {"title": "Chapter 3", "entities": {
        "places": {"剑门山": {"translation": "Jianmen Mountain", "last_chapter": 3}}}}
    tmpl = json.loads(_engine()._build_response_template(
        ["places"], {"places": {}}, chapter_number=9, base_template=base))
    assert tmpl["entities"]["places"]["剑门山"] == {"translation": "Jianmen Mountain"}


def test_gemini_schema_does_not_bill_for_it():
    from providers.gemini_provider import GeminiProvider
    schema = GeminiProvider.__new__(GeminiProvider)._create_response_schema(
        {"type": "json_object", "categories": ["characters", "places"],
         "gendered_categories": ["characters"]})
    entity_props = schema["properties"]["entities"]["properties"]
    assert "last_chapter" not in json.dumps(entity_props)
    assert entity_props["characters"]["properties"]["example"]["properties"]["gender"]


# ── nor shows it in the glossary it is given ─────────────────────────────────

def test_the_pre_translated_entities_block_carries_no_chapter(db):
    """"exact" restated the current chapter on every matched entity, and
    "similar" the stored one. Neither told the model anything it could act on."""
    entities = {
        "钟岳": {"translation": "Zhong Yue", "last_chapter": 40},
        "上山剑诀": {"translation": "Mountain Ascent Sword Art", "last_chapter": 12},
    }
    res = db.entities_inside_text(["钟岳走上山。"], entities, 41, do_count=False)
    assert res["exact"]["钟岳"] == {"translation": "Zhong Yue"}
    assert res["similar"]["上山剑诀"] == {
        "translation": "Mountain Ascent Sword Art", "match": "prefix"}


# ── and every persisting path stamps it ──────────────────────────────────────

def test_chunk_merge_overrides_whatever_the_model_said():
    merged = _engine().combine_json_chunks(
        {"content": [], "summary": "", "entities": {}},
        {"content": [], "summary": "", "entities": {
            "characters": {"钟岳": {"translation": "Zhong Yue", "last_chapter": 3}}}},
        41)
    assert merged["entities"]["characters"]["钟岳"]["last_chapter"] == 41
