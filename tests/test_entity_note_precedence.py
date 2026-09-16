"""Entity `note` precedence on the *entities* channel.

The first-pass translation model routinely re-emits entities that already exist,
and it may volunteer a `note` for them. A volunteered note must never clobber a
curated one. A note set by a human during review must win. Revising a note the
model already knows about is what the separate `note_updates` channel is for.

The rule used to live in the SQL as a COALESCE direction chosen per entity:
    human-edited  -> note = COALESCE(?, note)   reviewer's note wins
    model-emitted -> note = COALESCE(note, ?)   existing note always wins

It now lives in `UserInterface._write_entity_note`, in Python, because every
note write has to pass through `set_entity_note` to be recorded in
entity_note_revisions — a note's history is only reconstructable if nothing
writes the column behind its back. These tests pin the behaviour rather than
the SQL, and check the write is recorded.
"""
import pytest

from tests.conftest import FakeLogger


class _Writer:
    """The note-precedence half of UserInterface, without the translate loop."""

    from ui import UserInterface
    _write_entity_note = UserInterface._write_entity_note

    def __init__(self, entity_manager):
        self.entity_manager = entity_manager


@pytest.fixture
def entity(db):
    book_id = db.create_book("Precedence Book")

    def make(existing_note):
        db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id,
                      note=existing_note, note_author='human')
        entity_id = db.get_entity_id(book_id, "陈元")
        # Start each case with a clean history so "was it recorded?" is unambiguous.
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM entity_note_revisions WHERE entity_id = ?", (entity_id,))
            conn.commit()
        return entity_id

    return make


@pytest.mark.parametrize("existing,incoming,expected", [
    ("curated", "model note", "curated"),      # must NOT clobber
    (None, "model note", "model note"),        # fills an empty note
    ("curated", None, "curated"),              # omitting a note is harmless
    ("curated", "   ", "curated"),             # nor can whitespace clear it
])
def test_model_emitted_note_never_clobbers(db, entity, existing, incoming, expected):
    entity_id = entity(existing)
    _Writer(db)._write_entity_note(entity_id, existing, incoming,
                                   human_edited=False, chapter_number=12)
    assert db.get_entity_by_id(entity_id)["note"] == expected


@pytest.mark.parametrize("existing,incoming,expected", [
    ("curated", "reviewer note", "reviewer note"),  # reviewer wins
    (None, "reviewer note", "reviewer note"),
    ("curated", None, "curated"),                   # reviewer omitted one
])
def test_human_edited_note_wins(db, entity, existing, incoming, expected):
    entity_id = entity(existing)
    _Writer(db)._write_entity_note(entity_id, existing, incoming,
                                   human_edited=True, chapter_number=12)
    assert db.get_entity_by_id(entity_id)["note"] == expected


def test_a_write_is_recorded_with_its_author_and_chapter(db, entity):
    entity_id = entity(None)
    _Writer(db)._write_entity_note(entity_id, None, "First note on this entity.",
                                   human_edited=False, chapter_number=12)

    revisions = db.list_note_revisions(entity_id=entity_id)
    assert len(revisions) == 1
    assert revisions[0]["previous_note"] is None      # marks a note's creation
    assert revisions[0]["new_note"] == "First note on this entity."
    assert revisions[0]["author"] == "model"
    assert revisions[0]["chapter_number"] == 12


def test_a_refused_write_records_nothing(db, entity):
    entity_id = entity("curated")
    _Writer(db)._write_entity_note(entity_id, "curated", "model note",
                                   human_edited=False, chapter_number=12)
    assert db.list_note_revisions(entity_id=entity_id) == []


def test_ui_routes_notes_through_the_choke_point():
    """Regression guard: the entity save path must not write the note column
    itself — a direct write would be invisible to the history."""
    src = open("ui.py", encoding="utf-8").read()
    assert "human_edited_keys" in src
    assert "_write_entity_note" in src
    assert "COALESCE(note, ?)" not in src, "note writes must go through set_entity_note"
    assert "COALESCE(?, note)" not in src, "note writes must go through set_entity_note"
