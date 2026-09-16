"""Model-driven revisions to entity notes.

Notes are the translator's standing memory and ride into every prompt, so the
model was historically allowed to *create* one and never to change it. It may
now revise them through a dedicated `note_updates` channel. Nothing is locked;
the safety net is that every change — model, human or script — snapshots the
note it replaced into entity_note_revisions and can be reverted.

Two halves are covered here: the engine-side guards that decide what counts as
an applicable update, and the repo-side history that makes a bad one recoverable.
"""
import pytest

from tests.conftest import FakeLogger


# ---------------------------------------------------------------- engine side


class _EngineConfig:
    """Minimal config for TranslationEngine.validate_note_updates."""
    entity_note_updates = True


def _engine():
    from translation_engine import TranslationEngine

    return TranslationEngine(_EngineConfig(), FakeLogger(), entity_manager=None)


SNAPSHOT = {
    "characters": {
        "陈元": {"translation": "Chen Yuan", "note": "Male. Outer-sect disciple as of ch12."},
        "林霜": {"translation": "Lin Shuang", "note": ""},
    },
    "places": {
        "天海国": {"translation": "Heavenly Sea Kingdom"},
    },
}


def test_applicable_update_is_accepted_with_context():
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"note": "Male. Promoted to inner-sect disciple in ch212.",
                  "reason": "ch212 promotion"}},
        book_id=1, existing_entities=SNAPSHOT, chapter_number=212)

    assert len(out) == 1
    upd = out[0]
    assert upd["untranslated"] == "陈元"
    assert upd["category"] == "characters"
    assert upd["translation"] == "Chen Yuan"
    assert upd["old_note"] == "Male. Outer-sect disciple as of ch12."
    assert upd["new_note"] == "Male. Promoted to inner-sect disciple in ch212."
    assert upd["reason"] == "ch212 promotion"
    assert upd["chapter_number"] == 212
    assert upd["shrink"] is False


def test_unknown_entity_is_dropped():
    """The channel revises notes; it must never be a back door to new entities."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"某个人": {"note": "A stranger."}}, 1, SNAPSHOT, 5)
    assert out == []


def test_first_note_on_a_known_entity_is_allowed():
    """An entity with no note yet is still an existing entity — this is how a
    note gets attached to something the model met long before notes mattered."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"林霜": {"note": "Female. Uses 'this one' in formal speech."}}, 1, SNAPSHOT, 40)
    assert len(out) == 1
    assert out[0]["old_note"] == ""
    assert out[0]["shrink"] is False


def test_noop_is_dropped():
    """Models re-emit what they were shown; an echo is not a change."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"note": "  Male.  Outer-sect   disciple as of ch12. "}}, 1, SNAPSHOT, 30)
    assert out == []


def test_empty_note_is_dropped():
    """Clearing a note stays a human action."""
    eng = _engine()
    assert eng.validate_note_updates({"陈元": {"note": "   "}}, 1, SNAPSHOT, 30) == []
    assert eng.validate_note_updates({"陈元": {}}, 1, SNAPSHOT, 30) == []


def test_overlong_note_is_dropped():
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"note": "x" * (eng.NOTE_UPDATE_MAX_CHARS + 1)}}, 1, SNAPSHOT, 30)
    assert out == []


def test_per_chapter_cap_bounds_the_blast_radius():
    """Changes are meant to be rare; a chatty chapter can't rewrite the glossary."""
    eng = _engine()
    snapshot = {"characters": {
        f"人{i}": {"translation": f"Person {i}", "note": f"note {i}"} for i in range(6)
    }}
    raw = {f"人{i}": {"note": f"revised note {i}"} for i in range(6)}

    out = eng.validate_note_updates(raw, 1, snapshot, 30)
    assert len(out) == eng.NOTE_UPDATE_MAX_PER_CHAPTER


def test_shrink_is_flagged_not_blocked():
    """Losing more than half the note is the shape a clobber takes. It still
    applies — versioning is the safety net — but the audit panel badges it."""
    eng = _engine()
    out = eng.validate_note_updates(
        {"陈元": {"note": "Male."}}, 1, SNAPSHOT, 30)
    assert len(out) == 1
    assert out[0]["shrink"] is True


def test_disabled_setting_ignores_the_channel():
    eng = _engine()
    eng.config.entity_note_updates = False
    try:
        assert eng.validate_note_updates(
            {"陈元": {"note": "Something new entirely."}}, 1, SNAPSHOT, 30) == []
    finally:
        eng.config.entity_note_updates = True


def test_chunk_merge_keeps_notes_and_merges_updates():
    """combine_json_chunks rebuilds new-entity dicts field by field, and used to
    drop `note` for anything first seen after chunk 1."""
    eng = _engine()
    chunk1 = {"content": [], "summary": "a",
              "entities": {"characters": {"陈元": {"translation": "Chen Yuan"}}},
              "note_updates": {"陈元": {"note": "from chunk 1"}}}
    chunk2 = {"content": [], "summary": "b",
              "entities": {"characters": {
                  "林霜": {"translation": "Lin Shuang", "note": "Female, sword cultivator."}}},
              "note_updates": {"天海国": {"note": "from chunk 2"}}}

    merged = eng.combine_json_chunks(chunk1, chunk2, 212)

    assert merged["entities"]["characters"]["林霜"]["note"] == "Female, sword cultivator."
    assert set(merged["note_updates"]) == {"陈元", "天海国"}


# ------------------------------------------------------------------ repo side


@pytest.fixture
def book_with_entity(db):
    book_id = db.create_book("Test Book", author="Nobody")
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id,
                  note="Male. Outer-sect disciple as of ch12.")
    entity_id = db.get_entity_id(book_id, "陈元")
    assert entity_id
    return book_id, entity_id


def test_set_entity_note_records_the_note_it_replaced(db, book_with_entity):
    book_id, entity_id = book_with_entity

    revision_id = db.set_entity_note(entity_id, "Male. Inner-sect as of ch212.",
                                     author='model', chapter_number=212,
                                     reason="ch212 promotion")
    assert revision_id

    assert db.get_entity_by_id(entity_id)["note"] == "Male. Inner-sect as of ch212."

    # Two rows: the note's creation (previous_note NULL) and this change.
    revisions = db.list_note_revisions(book_id=book_id)
    assert len(revisions) == 2
    assert revisions[-1]["previous_note"] is None
    assert revisions[-1]["new_note"] == "Male. Outer-sect disciple as of ch12."
    rev = revisions[0]
    assert rev["previous_note"] == "Male. Outer-sect disciple as of ch12."
    assert rev["new_note"] == "Male. Inner-sect as of ch212."
    assert rev["author"] == "model"
    assert rev["chapter_number"] == 212
    assert rev["reason"] == "ch212 promotion"
    assert rev["untranslated"] == "陈元"
    assert rev["is_current"] is True


def test_identical_note_writes_nothing(db, book_with_entity):
    _, entity_id = book_with_entity
    before = db.list_note_revisions(entity_id=entity_id)
    assert db.set_entity_note(entity_id, "Male. Outer-sect disciple as of ch12.") is None
    assert db.list_note_revisions(entity_id=entity_id) == before


def test_revert_restores_the_previous_note_and_is_itself_recorded(db, book_with_entity):
    book_id, entity_id = book_with_entity
    original = db.get_entity_by_id(entity_id)["note"]

    revision_id = db.set_entity_note(entity_id, "A rewrite that lost the useful part.",
                                     author='model', chapter_number=300)
    db.revert_note_revision(revision_id)

    assert db.get_entity_by_id(entity_id)["note"] == original
    # creation + the rewrite + the revert
    revisions = db.list_note_revisions(book_id=book_id)
    assert len(revisions) == 3
    assert revisions[0]["author"] == "human"
    assert revisions[0]["new_note"] == original
    # The rewrite is no longer the note's current value, so it isn't offered for
    # revert a second time.
    assert revisions[1]["is_current"] is False


def test_history_is_never_pruned(db, book_with_entity):
    """Every note revision is kept forever.

    notes_as_of() rewinds through an entity's EARLIEST revisions, so a
    newest-N cap would throw away exactly the rows point-in-time lookup
    depends on. The history is append-only.
    """
    _, entity_id = book_with_entity
    writes = 25
    for i in range(writes):
        db.set_entity_note(entity_id, f"note version {i}", author='model')

    revisions = db.list_note_revisions(entity_id=entity_id, limit=200)
    # creation (from the fixture's note) + one per write
    assert len(revisions) == writes + 1
    # Newest first...
    assert revisions[0]["new_note"] == f"note version {writes - 1}"
    # ...and the creation record at the far end is still there.
    assert revisions[-1]["previous_note"] is None


def test_revisions_scope_to_their_book(db, book_with_entity):
    book_id, entity_id = book_with_entity
    other_book = db.create_book("Other Book")
    db.add_entity("characters", "李四", "Li Si", book_id=other_book, note="Someone else.")
    other_entity = db.get_entity_id(other_book, "李四")

    db.set_entity_note(entity_id, "Changed here.", author='model')
    db.set_entity_note(other_entity, "Changed there.", author='model')

    assert {r["untranslated"] for r in db.list_note_revisions(book_id=book_id)} == {"陈元"}
    assert {r["untranslated"] for r in db.list_note_revisions(book_id=other_book)} == {"李四"}


# ------------------------------------------------------------------- HTTP API


class TestNoteRevisionApi:
    """The audit surface: a note change applied unattended (review off) has to
    be findable and undoable afterwards."""

    def _seed(self, admin_client):
        book = admin_client.post("/api/books", json={"title": "Note API Book"}).json()["id"]
        resp = admin_client.post("/api/entities", json={
            "category": "characters", "untranslated": "陈元", "translation": "Chen Yuan",
            "book_id": book, "note": "Male. Outer-sect disciple.",
        })
        assert resp.status_code == 200, resp.text
        listing = admin_client.get(f"/api/entities?book_id={book}").json()
        entity_id = listing["entities"][0]["id"]
        return book, entity_id

    def test_admin_note_edit_is_versioned_and_revertible(self, admin_client):
        book, entity_id = self._seed(admin_client)

        resp = admin_client.put(f"/api/entities/{entity_id}",
                                json={"note": "Male. Inner-sect as of ch212."})
        assert resp.status_code == 200, resp.text

        revisions = admin_client.get(
            f"/api/entities/note-revisions?book_id={book}").json()["revisions"]
        # The note's creation is a revision too, so both writes are on the timeline.
        assert len(revisions) == 2
        assert revisions[-1]["previous_note"] is None
        assert revisions[0]["author"] == "human"
        assert revisions[0]["previous_note"] == "Male. Outer-sect disciple."

        resp = admin_client.post(
            f"/api/entities/note-revisions/{revisions[0]['id']}/revert", json={})
        assert resp.status_code == 200, resp.text

        entity = admin_client.get(f"/api/entities?book_id={book}").json()["entities"][0]
        assert entity["note"] == "Male. Outer-sect disciple."

    def test_unknown_revision_404s(self, admin_client):
        assert admin_client.post("/api/entities/note-revisions/999999/revert",
                                 json={}).status_code == 404

    def test_route_is_not_shadowed_by_entity_id_paths(self, admin_client):
        """/note-revisions is a literal segment declared after /{entity_id}
        routes; a regression here would surface as a 422 int-parse failure."""
        assert admin_client.get("/api/entities/note-revisions?limit=1").status_code == 200


# ------------------------------------------------------- review-decision merge


class _Reviewer:
    """The decision-merging half of WebInterface, without the WS machinery."""

    from web.services.web_interface import WebInterface
    _resolve_note_decisions = WebInterface._resolve_note_decisions


PROPOSED = [
    {"entity_id": 7, "untranslated": "陈元", "new_note": "Model's proposal."},
    {"entity_id": 8, "untranslated": "林霜", "new_note": "Second proposal."},
]


def test_omitted_decision_keeps_the_proposal():
    """Approve takes what's on screen — the same rule entity rows follow."""
    out = _Reviewer()._resolve_note_decisions(PROPOSED, {})
    assert [u["new_note"] for u in out] == ["Model's proposal.", "Second proposal."]


def test_rejected_decision_drops_it():
    out = _Reviewer()._resolve_note_decisions(PROPOSED, {"7": {"rejected": True}})
    assert [u["untranslated"] for u in out] == ["林霜"]


def test_edited_text_is_attributed_to_the_human():
    out = _Reviewer()._resolve_note_decisions(
        PROPOSED, {"7": {"note": "What I actually want it to say."}})
    assert out[0]["new_note"] == "What I actually want it to say."
    assert out[0]["author"] == "human"
    # Untouched proposal keeps model attribution (no author key added).
    assert "author" not in out[1]


def test_missing_decision_map_applies_nothing():
    """Skip Review, or a client too old to send the field: notes are left alone
    rather than changed by someone who never saw them."""
    assert _Reviewer()._resolve_note_decisions(PROPOSED, None) == []


# ----------------------------------------------- point-in-time reconstruction


class TestNotesAsOf:
    """A note's whole life is on the timeline, so the glossary can be read back
    as it stood at any chapter — what get_entities.py --chapters N-M needs."""

    def _book(self, db):
        book_id = db.create_book("Timeline Book")
        db.add_entity("characters", "许春娘", "Xu Chunniang", book_id=book_id,
                      last_chapter=1, origin_chapter=1,
                      note="Protagonist. Eight years old, village girl.",
                      note_author='model', note_chapter=1)
        entity_id = db.get_entity_id(book_id, "许春娘")
        db.set_entity_note(entity_id, "Protagonist. Eight years old, outer-sect disciple.",
                           author='model', chapter_number=34)
        db.set_entity_note(entity_id, "Protagonist. Past forty; fourth level of Foundation.",
                           author='model', chapter_number=243)
        return book_id, entity_id

    def test_rewinds_to_the_note_in_force_at_that_chapter(self, db):
        book_id, entity_id = self._book(db)

        assert db.notes_as_of(book_id, 10)[entity_id] == \
            "Protagonist. Eight years old, village girl."
        assert db.notes_as_of(book_id, 33)[entity_id] == \
            "Protagonist. Eight years old, village girl."
        # A note written at ch34 counts as in force at ch34 — the state at the
        # END of that chapter, which is what a chapter-range view wants.
        assert db.notes_as_of(book_id, 34)[entity_id] == \
            "Protagonist. Eight years old, outer-sect disciple."
        assert db.notes_as_of(book_id, 100)[entity_id] == \
            "Protagonist. Eight years old, outer-sect disciple."
        assert db.notes_as_of(book_id, 9999)[entity_id] == \
            "Protagonist. Past forty; fourth level of Foundation."

    def test_before_the_note_existed_is_none(self, db):
        book_id = db.create_book("Late Note Book")
        db.add_entity("characters", "苏尘", "Su Chen", book_id=book_id,
                      last_chapter=5, origin_chapter=5)
        entity_id = db.get_entity_id(book_id, "苏尘")
        db.set_entity_note(entity_id, "Sect elder, speaks formally.",
                           author='model', chapter_number=40)

        # Entity existed from ch5, but carried no note until ch40.
        assert db.notes_as_of(book_id, 20)[entity_id] is None
        assert db.notes_as_of(book_id, 39)[entity_id] is None
        assert db.notes_as_of(book_id, 40)[entity_id] == "Sect elder, speaks formally."

    def test_entity_that_did_not_exist_yet_has_no_note(self, db):
        book_id = db.create_book("Origin Floor Book")
        db.add_entity("characters", "莫林", "Mo Lin", book_id=book_id,
                      last_chapter=300, origin_chapter=300, note="Late arrival.",
                      note_author='model', note_chapter=300)
        entity_id = db.get_entity_id(book_id, "莫林")

        assert db.notes_as_of(book_id, 100)[entity_id] is None
        assert db.notes_as_of(book_id, 300)[entity_id] == "Late arrival."

    def test_hand_edits_are_not_rewound(self, db):
        """A correction you made by hand carries no chapter; it belongs to the
        present and stays applied to the historical view."""
        book_id, entity_id = self._book(db)
        db.set_entity_note(entity_id, "Protagonist. Past forty. (fixed by hand)",
                           author='human')

        assert db.notes_as_of(book_id, 9999)[entity_id].endswith("(fixed by hand)")
        # Rewinding past the ch243 model change still lands on the older text.
        assert db.notes_as_of(book_id, 100)[entity_id] == \
            "Protagonist. Eight years old, outer-sect disciple."

    def test_scopes_to_the_requested_entities(self, db):
        book_id, entity_id = self._book(db)
        db.add_entity("places", "无妄山", "Boundless Mountain", book_id=book_id,
                      last_chapter=2, origin_chapter=2, note="A mountain.",
                      note_author='model', note_chapter=2)

        assert set(db.notes_as_of(book_id, 50, entity_ids=[entity_id])) == {entity_id}
        assert len(db.notes_as_of(book_id, 50)) == 2


# ------------------------------------------- retranslation sees historic notes


class _RewindEngine:
    """TranslationEngine.apply_historic_notes over a real DatabaseManager."""

    from translation_engine import TranslationEngine
    apply_historic_notes = TranslationEngine.apply_historic_notes

    def __init__(self, db):
        self.entity_manager = db
        self.logger = FakeLogger()


@pytest.fixture
def aged_book(db):
    """A book whose protagonist's note has moved on since chapter 34."""
    book_id = db.create_book("Aged Book")
    db.add_entity("characters", "许春娘", "Xu Chunniang", book_id=book_id,
                  last_chapter=1, origin_chapter=1,
                  note="Protagonist. Eight years old, village girl.",
                  note_author='model', note_chapter=1)
    entity_id = db.get_entity_id(book_id, "许春娘")
    db.set_entity_note(entity_id, "Protagonist. Past forty; fourth level of Foundation.",
                       author='model', chapter_number=243)
    return book_id


def _snapshot(db, book_id):
    return db.get_entities_snapshot(book_id)


def test_retranslating_an_old_chapter_sees_the_old_note(db, aged_book):
    """The whole point: chapter 34 must not be retranslated against chapter-243
    facts about the protagonist."""
    entities = _snapshot(db, aged_book)
    historic = _RewindEngine(db).apply_historic_notes(entities, aged_book, 34)

    assert historic is True
    assert entities["characters"]["许春娘"]["note"] == \
        "Protagonist. Eight years old, village girl."


def test_translating_a_new_chapter_is_untouched(db, aged_book):
    entities = _snapshot(db, aged_book)
    historic = _RewindEngine(db).apply_historic_notes(entities, aged_book, 400)

    assert historic is False
    assert entities["characters"]["许春娘"]["note"] == \
        "Protagonist. Past forty; fourth level of Foundation."


def test_a_note_written_later_is_removed_not_blanked(db, aged_book):
    """An entity whose note did not exist yet must reach the prompt with no note
    at all, rather than an empty string the template would still render."""
    db.add_entity("characters", "莫林", "Mo Lin", book_id=aged_book,
                  last_chapter=300, origin_chapter=300, note="Late arrival.",
                  note_author='model', note_chapter=300)

    entities = _snapshot(db, aged_book)
    _RewindEngine(db).apply_historic_notes(entities, aged_book, 34)

    assert "note" not in entities["characters"]["莫林"]


def test_translations_are_never_rewound(db, aged_book):
    """Renderings must stay consistent across the whole book; only notes move."""
    entities = _snapshot(db, aged_book)
    _RewindEngine(db).apply_historic_notes(entities, aged_book, 34)

    assert entities["characters"]["许春娘"]["translation"] == "Xu Chunniang"


def test_has_note_revisions_after_is_the_gate(db, aged_book):
    assert db.has_note_revisions_after(aged_book, 34) is True
    assert db.has_note_revisions_after(aged_book, 243) is False
    assert db.has_note_revisions_after(aged_book, 999) is False
