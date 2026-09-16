"""The CLI substitution helpers in correct_entity_translation.py — shared with
bulk_correct_entities.py, which imports them wholesale.

Focus: the note sweep that rides along with the chapter sweep (entity notes are
fed back into later translations, so a stale note re-seeds the corrected term),
and the {chapter_id: chapter_number} contract that scopes it.
"""
import pytest

from correct_entity_translation import (
    count_note_substitutions,
    count_substitutions,
    find_chapters_with_untranslated,
    note_scope_for,
    substitute_in_chapters,
)


@pytest.fixture
def book(db):
    book_id = db.create_book(title="CLI Test Book")
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


class TestFindChaptersWithUntranslated:
    def test_maps_ids_to_chapter_numbers(self, db, book):
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
        _save_chapter(db, book, 9, ["无关的一章。"], ["An unrelated chapter."])

        found = find_chapters_with_untranslated(db, book, "青云宗")

        assert list(found.values()) == [5]
        assert len(found) == 1  # len() still reads as "how many candidate chapters"


class TestNoteScopeFor:
    def test_none_stays_none(self):
        """No chapter restriction = book-wide prose sweep = book-wide note sweep."""
        assert note_scope_for(None) is None

    def test_dict_collapses_to_chapter_numbers(self):
        assert note_scope_for({11: 5, 12: 9}) == {5, 9}


class TestSubstituteInChapters:
    def test_safer_sweep_rewrites_prose_and_scoped_notes(self, db, book):
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
        _save_chapter(db, book, 9, ["无关的一章。"], ["The Azure Sword Sect is famous."])

        born_here = _add_entity(db, "李四", "Li Si", book, origin_chapter=5,
                                note="An elder of the Azure Sword Sect.")
        born_elsewhere = _add_entity(db, "王五", "Wang Wu", book, origin_chapter=9,
                                     note="A rival of the Azure Sword Sect.")

        chapter_ids = find_chapters_with_untranslated(db, book, "青云宗")
        affected, notes = substitute_in_chapters(
            db, book, "Azure Sword Sect", "Cyan Blade Sect", chapter_ids,
        )

        assert (affected, notes) == (1, 1)
        # Chapter 9 is outside the safer subset: its prose keeps the old English,
        # and so does the note of the entity born there.
        assert db.get_chapter(book_id=book, chapter_number=5)["content"] == [
            "A disciple of the Cyan Blade Sect."]
        assert db.get_chapter(book_id=book, chapter_number=9)["content"] == [
            "The Azure Sword Sect is famous."]
        assert db.get_entity_by_id(born_here)["note"] == "An elder of the Cyan Blade Sect."
        assert db.get_entity_by_id(born_elsewhere)["note"] == "A rival of the Azure Sword Sect."

    def test_book_wide_sweep_takes_every_note(self, db, book):
        _save_chapter(db, book, 1, ["青云宗。"], ["The Azure Sword Sect."])
        no_origin = _add_entity(db, "王五", "Wang Wu", book,
                                note="Guards the Azure Sword Sect gate.")

        affected, notes = substitute_in_chapters(
            db, book, "Azure Sword Sect", "Cyan Blade Sect", None,
        )

        assert (affected, notes) == (1, 1)
        assert db.get_entity_by_id(no_origin)["note"] == "Guards the Cyan Blade Sect gate."

    def test_dry_run_count_matches_the_apply_run(self, db, book):
        """--dry-run must not lie: the same note scope, counted not written."""
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
        eid = _add_entity(db, "李四", "Li Si", book, origin_chapter=5,
                          note="An elder of the Azure Sword Sect.")

        chapter_ids = find_chapters_with_untranslated(db, book, "青云宗")
        predicted = count_note_substitutions(
            db, book, "Azure Sword Sect", "Cyan Blade Sect", chapter_ids,
        )
        assert predicted == 1
        assert db.get_entity_by_id(eid)["note"] == "An elder of the Azure Sword Sect."

        _, notes = substitute_in_chapters(
            db, book, "Azure Sword Sect", "Cyan Blade Sect", chapter_ids,
        )
        assert notes == predicted

    def test_sweep_rewrites_the_chapter_title_too(self, db, book):
        """Titles live in their own column and were being left stale.

        Book 93's ch1 kept "The Sky Dog Devours the Sun" in its title long after
        the prose had been corrected to "Celestial Dog"; the Reader, the TOC and
        every EPUB export read the title column.
        """
        assert db.save_chapter(
            book_id=book, chapter_number=1,
            title="The Sky Dog Devours the Sun",
            untranslated_content=["天狗食日。"],
            translated_content=["The Sky Dog is devouring the sun!"],
        )

        affected, _ = substitute_in_chapters(
            db, book, "Sky Dog", "Celestial Dog", None,
        )

        assert affected == 1
        ch = db.get_chapter(book_id=book, chapter_number=1)
        assert ch["title"] == "The Celestial Dog Devours the Sun"
        assert ch["content"] == ["The Celestial Dog is devouring the sun!"]

    def test_title_only_match_is_still_written_and_counted(self, db, book):
        """A chapter whose title alone matches must not be skipped."""
        assert db.save_chapter(
            book_id=book, chapter_number=1,
            title="Escape from Cotton-Cloth Town",
            untranslated_content=["无关。"],
            translated_content=["Nothing matching here."],
        )

        affected, _ = substitute_in_chapters(
            db, book, "Cotton-Cloth Town", "Commoner Town", None,
        )

        assert affected == 1
        ch = db.get_chapter(book_id=book, chapter_number=1)
        assert ch["title"] == "Escape from Commoner Town"
        assert ch["content"] == ["Nothing matching here."]

    def test_dry_run_count_includes_title_only_matches(self, db, book):
        """--dry-run must not under-report the chapters an apply run will touch."""
        assert db.save_chapter(
            book_id=book, chapter_number=1,
            title="Escape from Cotton-Cloth Town",
            untranslated_content=["无关。"],
            translated_content=["Nothing matching here."],
        )

        predicted = count_substitutions(db, book, "Cotton-Cloth Town", "Commoner Town")
        assert predicted == 1
        # Counting must not have written anything.
        assert db.get_chapter(book_id=book, chapter_number=1)["title"] == \
            "Escape from Cotton-Cloth Town"

        affected, _ = substitute_in_chapters(
            db, book, "Cotton-Cloth Town", "Commoner Town", None,
        )
        assert affected == predicted

    def test_title_sweep_respects_the_safer_chapter_scope(self, db, book):
        """--safer-substitute must gate titles by the same chapter subset as prose."""
        _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
        assert db.save_chapter(
            book_id=book, chapter_number=9,
            title="The Azure Sword Sect at Dawn",
            untranslated_content=["无关的一章。"],
            translated_content=["An unrelated chapter."],
        )

        chapter_ids = find_chapters_with_untranslated(db, book, "青云宗")
        affected, _ = substitute_in_chapters(
            db, book, "Azure Sword Sect", "Cyan Blade Sect", chapter_ids,
        )

        assert affected == 1
        # ch9's source lacks 青云宗, so neither its prose nor its title is touched.
        assert db.get_chapter(book_id=book, chapter_number=9)["title"] == \
            "The Azure Sword Sect at Dawn"

    def test_word_boundary_applies_to_notes_too(self, db, book):
        _save_chapter(db, book, 5, ["黛。"], ["Dai and Daiyu."])
        eid = _add_entity(db, "李四", "Li Si", book, origin_chapter=5,
                          note="Serves Dai, not Daiyu.")

        affected, notes = substitute_in_chapters(
            db, book, "Dai", "Tai", None, word_boundary=True,
        )

        assert (affected, notes) == (1, 1)
        assert db.get_chapter(book_id=book, chapter_number=5)["content"] == ["Tai and Daiyu."]
        assert db.get_entity_by_id(eid)["note"] == "Serves Tai, not Daiyu."
