"""POST /api/entities/propagate (action="substitute") — the endpoint behind both
entity modals (EntityFormModal's PropagateModal and RetroactiveReviewModal's
PropagateOverlay).

Focus: the note sweep that rides along with the chapter sweep. Entity notes are
fed back into later translations, so a note left holding the old English would
re-seed the term that was just corrected.
"""
import pytest


@pytest.fixture
def book(db):
    book_id = db.create_book(title="Propagate Test Book")
    assert book_id
    return book_id


def _add_entity(db, untranslated, translation, book_id, **kw):
    assert db.add_entity("characters", untranslated, translation, book_id=book_id, **kw)
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM entities WHERE untranslated = ?", (untranslated,))
        return cur.fetchone()["id"]


def _save_chapter(db, book_id, number, source, translated):
    assert db.save_chapter(
        book_id=book_id,
        chapter_number=number,
        title=f"Chapter {number}",
        untranslated_content=source,
        translated_content=translated,
    )


def _propagate(client, **body):
    res = client.post("/api/entities/propagate", json=body)
    assert res.status_code == 200, res.text
    return res.json()


class TestPropagateNoteSweep:
    def test_safer_substitute_rewrites_notes_of_that_chapters_entities(
        self, db, admin_client, book
    ):
        """The literal ask: substituting in a chapter also fixes the notes of the
        entities that originated in it."""
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
        _save_chapter(db, book, 9, ["无关的一章。"], ["An unrelated chapter."])

        sect = _add_entity(db, "青云宗", "Azure Sword Sect", book, origin_chapter=5)
        born_here = _add_entity(db, "李四", "Li Si", book, origin_chapter=5,
                                note="An elder of the Azure Sword Sect.")
        born_elsewhere = _add_entity(db, "王五", "Wang Wu", book, origin_chapter=9,
                                     note="A rival of the Azure Sword Sect.")

        res = _propagate(
            admin_client,
            entity_id=sect,
            old_translation="Azure Sword Sect",
            new_translation="Cyan Blade Sect",
            action="substitute",
            safer=True,
        )

        assert res["affected"] == 1
        assert res["notes_affected"] == 1

        # Chapter 5's prose was rewritten...
        ch5 = db.get_chapter(book_id=book, chapter_number=5)
        assert ch5["content"] == ["A disciple of the Cyan Blade Sect."]
        # ...and so was the note of the entity that originated there.
        assert db.get_entity_by_id(born_here)["note"] == "An elder of the Cyan Blade Sect."
        # Chapter 9 was out of scope (its source never mentions 青云宗), so the
        # entity born there keeps its note.
        assert db.get_entity_by_id(born_elsewhere)["note"] == "A rival of the Azure Sword Sect."

    def test_book_wide_substitute_sweeps_every_note(self, db, admin_client, book):
        """"Replace in all chapters" is book-wide for notes too — including
        entities with no origin_chapter, which no chapter set could match."""
        _save_chapter(db, book, 1, ["青云宗。"], ["The Azure Sword Sect."])

        sect = _add_entity(db, "青云宗", "Azure Sword Sect", book, origin_chapter=1)
        no_origin = _add_entity(db, "王五", "Wang Wu", book,
                                note="Guards the Azure Sword Sect gate.")

        res = _propagate(
            admin_client,
            entity_id=sect,
            old_translation="Azure Sword Sect",
            new_translation="Cyan Blade Sect",
            action="substitute",
        )

        assert res["notes_affected"] == 1
        assert db.get_entity_by_id(no_origin)["note"] == "Guards the Cyan Blade Sect gate."

    def test_from_chapter_scopes_the_note_sweep(self, db, admin_client, book):
        """RetroactiveReviewModal always sends from_chapter — notes follow the
        same restricted blast radius as the prose."""
        _save_chapter(db, book, 1, ["青云宗。"], ["The Azure Sword Sect."])
        _save_chapter(db, book, 7, ["青云宗。"], ["The Azure Sword Sect."])

        sect = _add_entity(db, "青云宗", "Azure Sword Sect", book, origin_chapter=1)
        early = _add_entity(db, "李四", "Li Si", book, origin_chapter=1,
                            note="An elder of the Azure Sword Sect.")
        late = _add_entity(db, "王五", "Wang Wu", book, origin_chapter=7,
                           note="A rival of the Azure Sword Sect.")

        res = _propagate(
            admin_client,
            entity_id=sect,
            old_translation="Azure Sword Sect",
            new_translation="Cyan Blade Sect",
            action="substitute",
            from_chapter=7,
        )

        assert res["notes_affected"] == 1
        assert db.get_entity_by_id(early)["note"] == "An elder of the Azure Sword Sect."
        assert db.get_entity_by_id(late)["note"] == "A rival of the Cyan Blade Sect."

    def test_note_is_swept_even_when_no_chapter_prose_changed(self, db, admin_client, book):
        """The stale-note case that motivated this: the prose of the origin
        chapter never used the old wording, but the extraction note did."""
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the sect."])

        sect = _add_entity(db, "青云宗", "Azure Sword Sect", book, origin_chapter=5)
        born_here = _add_entity(db, "李四", "Li Si", book, origin_chapter=5,
                                note="An elder of the Azure Sword Sect.")

        res = _propagate(
            admin_client,
            entity_id=sect,
            old_translation="Azure Sword Sect",
            new_translation="Cyan Blade Sect",
            action="substitute",
            safer=True,
        )

        assert res["affected"] == 0
        assert res["notes_affected"] == 1
        assert db.get_entity_by_id(born_here)["note"] == "An elder of the Cyan Blade Sect."
