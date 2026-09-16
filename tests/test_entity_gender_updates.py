"""Model-driven corrections to an entity's gender.

Gender decides the pronouns an entity gets in every later chapter, and source
languages routinely omit the pronoun that would settle it — so an early guess is
wrong often enough to matter, and nothing in the prose shows it the way a wrong
name would. The model may now correct one through the same `note_updates`
channel it revises notes on.

Unlike notes this is deliberately NOT point-in-time: there is no genders_as_of,
the current gender is the only truth, and a character who genuinely changes
gender mid-story is handled by correcting the record and saying so in the note.
What makes a bad correction survivable is the same thing that makes a bad note
survivable — every change snapshots the value it replaced into
entity_gender_revisions and can be reverted.
"""
import pytest

from tests.conftest import FakeLogger


# ---------------------------------------------------------------- engine side


class _EngineConfig:
    entity_note_updates = True


def _engine():
    from translation_engine import TranslationEngine

    return TranslationEngine(_EngineConfig(), FakeLogger(), entity_manager=None)


SNAPSHOT = {
    "characters": {
        "陈元": {"translation": "Chen Yuan", "gender": "male",
                 "note": "Male. Outer-sect disciple as of ch12."},
        "林霜": {"translation": "Lin Shuang"},
    },
    "places": {
        "天海国": {"translation": "Heavenly Sea Kingdom"},
    },
}

GENDERED = ["characters"]


def test_gender_correction_rides_the_note_channel():
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"gender": "female", "reason": "ch212 reveals she was disguised"}},
        book_id=1, existing_entities=SNAPSHOT, chapter_number=212,
        gendered_categories=GENDERED)

    assert len(out) == 1
    upd = out[0]
    assert upd["untranslated"] == "陈元"
    assert upd["old_gender"] == "male"
    assert upd["new_gender"] == "female"
    # Gender-only: the note half is untouched, not blanked.
    assert upd["new_note"] is None
    assert upd["old_note"] == "Male. Outer-sect disciple as of ch12."
    assert upd["shrink"] is False


def test_note_and_gender_travel_together():
    """The chapter that settles a character's gender usually rewrites the note
    too — one entry carries both."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"note": "Female. Disguised as a male disciple until ch212.",
                  "gender": "female", "reason": "ch212 unmasking"}},
        1, SNAPSHOT, 212, gendered_categories=GENDERED)

    assert len(out) == 1
    assert out[0]["new_gender"] == "female"
    assert out[0]["new_note"].startswith("Female.")


def test_gender_on_a_category_that_does_not_track_it_is_dropped():
    """A kingdom has no gender to correct; the book's own gendered_categories
    decides, not the model."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"天海国": {"gender": "female"}}, 1, SNAPSHOT, 30,
        gendered_categories=GENDERED)
    assert out == []


def test_note_still_applies_when_the_gender_half_is_rejected():
    eng = _engine()
    out = eng.validate_note_updates(
        {"天海国": {"note": "Rendered 'Heavenly Sea Kingdom' throughout.",
                   "gender": "female"}},
        1, SNAPSHOT, 30, gendered_categories=GENDERED)

    assert len(out) == 1
    assert out[0]["new_gender"] is None
    assert out[0]["new_note"] == "Rendered 'Heavenly Sea Kingdom' throughout."


def test_invalid_gender_value_is_dropped():
    eng = _engine()
    for bad in ("woman", "F", "", "  ", 3, None):
        out = eng.validate_note_updates(
            {"陈元": {"gender": bad}}, 1, SNAPSHOT, 30, gendered_categories=GENDERED)
        assert out == [], bad


def test_gender_noop_is_dropped():
    """Models re-emit what they were shown; an echo is not a correction."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"gender": "MALE"}}, 1, SNAPSHOT, 30, gendered_categories=GENDERED)
    assert out == []


def test_first_gender_on_an_ungendered_entity_is_allowed():
    """An entity nobody has gendered yet is still an existing entity."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"林霜": {"gender": "female"}}, 1, SNAPSHOT, 40, gendered_categories=GENDERED)
    assert len(out) == 1
    assert out[0]["old_gender"] is None
    assert out[0]["new_gender"] == "female"


def test_gendered_categories_defaults_to_characters():
    """No book context (the legacy default) still gender-tracks characters."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"gender": "female"}}, 1, SNAPSHOT, 30)
    assert len(out) == 1
    assert out[0]["new_gender"] == "female"


def test_unknown_entity_is_dropped():
    """The channel corrects records; it is never a back door to new entities."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"某个人": {"gender": "female"}}, 1, SNAPSHOT, 5, gendered_categories=GENDERED)
    assert out == []


def test_disabled_setting_ignores_gender_too():
    eng = _engine()
    eng.config.entity_note_updates = False
    try:
        assert eng.validate_note_updates(
            {"陈元": {"gender": "female"}}, 1, SNAPSHOT, 30,
            gendered_categories=GENDERED) == []
    finally:
        eng.config.entity_note_updates = True


def test_per_chapter_cap_counts_entries_not_halves():
    eng = _engine()
    snapshot = {"characters": {
        f"人{i}": {"translation": f"Person {i}", "gender": "male"} for i in range(6)
    }}
    raw = {f"人{i}": {"gender": "female"} for i in range(6)}

    out = eng.validate_note_updates(raw, 1, snapshot, 30, gendered_categories=GENDERED)
    assert len(out) == eng.NOTE_UPDATE_MAX_PER_CHAPTER


def test_prompt_section_only_appears_for_gender_tracked_books():
    from translation_engine import TranslationEngine

    assert TranslationEngine._gender_update_section([]) == ""
    assert TranslationEngine._gender_update_section(None) == ""
    section = TranslationEngine._gender_update_section(["characters", "creatures"])
    assert '"gender"' in section
    assert '"characters", "creatures"' in section
    # The transformation case is answered in the prompt, since the field itself
    # keeps no history.
    assert "note" in section


# ------------------------------------------------------------------ repo side


@pytest.fixture
def book_with_entity(db):
    book_id = db.create_book("Test Book", author="Nobody")
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, gender="male")
    entity_id = db.get_entity_id(book_id, "陈元")
    assert entity_id
    return book_id, entity_id


def test_set_entity_gender_records_what_it_replaced(db, book_with_entity):
    book_id, entity_id = book_with_entity

    revision_id = db.set_entity_gender(entity_id, "female", author='model',
                                       chapter_number=212, reason="ch212 unmasking")
    assert revision_id
    assert db.get_entity_by_id(entity_id)["gender"] == "female"

    revisions = db.list_gender_revisions(book_id=book_id)
    # Only changes are recorded — being born with a gender writes no row.
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev["previous_gender"] == "male"
    assert rev["new_gender"] == "female"
    assert rev["author"] == "model"
    assert rev["chapter_number"] == 212
    assert rev["reason"] == "ch212 unmasking"
    assert rev["untranslated"] == "陈元"
    assert rev["is_current"] is True


def test_identical_gender_writes_nothing(db, book_with_entity):
    _, entity_id = book_with_entity
    assert db.set_entity_gender(entity_id, "male") is None
    assert db.list_gender_revisions(entity_id=entity_id) == []


def test_invalid_gender_is_refused_at_the_choke_point(db, book_with_entity):
    _, entity_id = book_with_entity
    assert db.set_entity_gender(entity_id, "woman") is None
    assert db.get_entity_by_id(entity_id)["gender"] == "male"


def test_revert_restores_the_previous_gender_and_is_itself_recorded(db, book_with_entity):
    book_id, entity_id = book_with_entity

    revision_id = db.set_entity_gender(entity_id, "female", author='model',
                                       chapter_number=300)
    db.revert_gender_revision(revision_id)

    assert db.get_entity_by_id(entity_id)["gender"] == "male"
    revisions = db.list_gender_revisions(book_id=book_id)
    assert len(revisions) == 2
    assert revisions[0]["author"] == "human"
    assert revisions[0]["new_gender"] == "male"
    # The reverted change is no longer current, so it isn't offered again.
    assert revisions[1]["is_current"] is False


def test_revisions_scope_to_their_book(db, book_with_entity):
    book_id, entity_id = book_with_entity
    other_book = db.create_book("Other Book")
    db.add_entity("characters", "李四", "Li Si", book_id=other_book, gender="male")
    other_id = db.get_entity_id(other_book, "李四")

    db.set_entity_gender(entity_id, "female")
    db.set_entity_gender(other_id, "neutral")

    assert [r["untranslated"] for r in db.list_gender_revisions(book_id=book_id)] == ["陈元"]
    assert [r["untranslated"] for r in db.list_gender_revisions(book_id=other_book)] == ["李四"]


def test_entities_page_edit_lands_in_the_history(db, book_with_entity):
    """update_entity_by_id diverts `gender` the way it diverts `note`, so a
    human correction sits on the same timeline as the model's."""
    _, entity_id = book_with_entity

    db.update_entity_by_id(entity_id, gender="neutral", translation="Chen Yuan")

    assert db.get_entity_by_id(entity_id)["gender"] == "neutral"
    revisions = db.list_gender_revisions(entity_id=entity_id)
    assert len(revisions) == 1
    assert revisions[0]["author"] == "human"
    assert revisions[0]["previous_gender"] == "male"


def test_add_entity_on_an_existing_row_records_the_change(db, book_with_entity):
    book_id, entity_id = book_with_entity

    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, gender="female",
                  gender_author='script', gender_reason="bulk repair")

    assert db.get_entity_by_id(entity_id)["gender"] == "female"
    rev = db.list_gender_revisions(entity_id=entity_id)[0]
    assert rev["author"] == "script"
    assert rev["reason"] == "bulk repair"


def test_add_entity_without_a_gender_leaves_the_recorded_one_alone(db, book_with_entity):
    book_id, entity_id = book_with_entity

    db.add_entity("characters", "陈元", "Chen Yuan the Elder", book_id=book_id)

    assert db.get_entity_by_id(entity_id)["gender"] == "male"
    assert db.list_gender_revisions(entity_id=entity_id) == []


# ------------------------------------------------- entities-channel precedence


class _Writer:
    """The gender-precedence half of UserInterface, without the translate loop."""

    from ui import UserInterface
    _write_entity_gender = UserInterface._write_entity_gender

    def __init__(self, entity_manager):
        self.entity_manager = entity_manager


@pytest.mark.parametrize("existing,incoming,expected", [
    ("male", "female", "male"),      # must NOT clobber: one bad guess, whole book
    (None, "female", "female"),      # fills a gender nobody recorded
    ("male", None, "male"),          # omitting one is harmless
    ("male", "   ", "male"),         # nor can whitespace clear it
])
def test_model_emitted_gender_never_clobbers(db, existing, incoming, expected):
    """On the *entities* channel the model re-emits known entities every chapter,
    so a volunteered gender may only fill a blank. Changing a recorded one is
    what note_updates is for, where it is deliberate and reviewable."""
    book_id = db.create_book("Precedence Book")
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, gender=existing)
    entity_id = db.get_entity_id(book_id, "陈元")

    _Writer(db)._write_entity_gender(entity_id, existing, incoming,
                                     human_edited=False, chapter_number=12)
    assert (db.get_entity_by_id(entity_id)["gender"] or None) == expected


def test_human_edited_gender_wins(db):
    book_id = db.create_book("Precedence Book")
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, gender="male")
    entity_id = db.get_entity_id(book_id, "陈元")

    _Writer(db)._write_entity_gender(entity_id, "male", "female",
                                     human_edited=True, chapter_number=12)
    assert db.get_entity_by_id(entity_id)["gender"] == "female"
    assert db.list_gender_revisions(entity_id=entity_id)[0]["author"] == "human"


def test_ui_routes_genders_through_the_choke_point():
    """Regression guard: the entity save path must not write the gender column
    itself — a direct write would be invisible to the history and would let a
    re-emitted entity flip a character's pronouns unnoticed."""
    src = open("ui.py", encoding="utf-8").read()
    assert "_write_entity_gender" in src
    assert "incorrect_translation = ?, gender = ?" not in src, \
        "gender writes must go through set_entity_gender"
