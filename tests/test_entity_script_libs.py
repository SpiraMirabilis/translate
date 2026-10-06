"""Library entry points extracted from the entity-correction CLIs.

correct_entity (correct_entity_translation.py), bulk_correct
(bulk_correct_entities.py), plan/apply_entity_deletes (delete_entity.py) and
change_entity_categories (change_entity_category.py) — the functions the MCP
server imports. They must not print, not exit, and not write on a dry run.

Note: entities are UNIQUE(book_id, untranslated), so a key in two categories
cannot be built through the DB; the ambiguity paths are exercised by patching
the scripts' finder functions.
"""
import pytest

import bulk_correct_entities
import change_entity_category
import correct_entity_translation
import delete_entity
from bulk_correct_entities import bulk_correct, parse_correction
from change_entity_category import change_entity_categories
from correct_entity_translation import correct_entity
from delete_entity import apply_entity_deletes, plan_entity_deletes


@pytest.fixture
def book(db):
    book_id = db.create_book(title="Script Lib Book")
    assert book_id
    return book_id


@pytest.fixture
def epub_calls(db, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "invalidate_epub_cache", lambda book_id: calls.append(book_id))
    return calls


def _add_entity(db, book_id, untranslated, translation, category="characters", **kw):
    assert db.add_entity(category, untranslated, translation, book_id=book_id, **kw)
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM entities WHERE book_id = ? AND untranslated = ?",
                    (book_id, untranslated))
        return cur.fetchone()["id"]


def _save_chapter(db, book_id, number, source, translated, title=None):
    assert db.save_chapter(
        book_id=book_id, chapter_number=number,
        title=title or f"Chapter {number}",
        untranslated_content=source, translated_content=translated,
    )


def _content(db, book_id, number):
    return db.get_chapter(book_id=book_id, chapter_number=number)["content"]


@pytest.fixture
def sect_book(db, book):
    """ch5 mentions 青云宗 in its source; ch9 only carries the English."""
    _save_chapter(db, book, 5, ["青云宗的弟子。"], ["A disciple of the Azure Sword Sect."])
    _save_chapter(db, book, 9, ["无关的一章。"], ["The Azure Sword Sect is famous."])
    eid = _add_entity(db, book, "青云宗", "Azure Sword Sect", category="organizations")
    return book, eid


# --------------------------------------------------------------------------
# correct_entity
# --------------------------------------------------------------------------

class TestCorrectEntity:
    def test_mode_none_updates_record_only(self, db, sect_book, epub_calls):
        book, eid = sect_book
        r = correct_entity(db, book, "青云宗", "Cyan Blade Sect")

        assert r["ok"] and r["error"] is None and r["status"] == "updated"
        assert r["changed"] is True
        assert (r["entity_id"], r["category"]) == (eid, "organizations")
        assert (r["old_translation"], r["new_translation"]) == ("Azure Sword Sect", "Cyan Blade Sect")
        assert r["chapter_substitutions"] == 0 and r["note_substitutions"] == 0
        assert r["chapters_scanned"] is None
        ent = db.get_entity_by_id(eid)
        assert ent["translation"] == "Cyan Blade Sect"
        assert ent["incorrect_translation"] == "Azure Sword Sect"
        assert _content(db, book, 9) == ["The Azure Sword Sect is famous."]
        assert epub_calls == []

    def test_substitute_is_book_wide(self, db, sect_book, epub_calls):
        book, _ = sect_book
        r = correct_entity(db, book, "青云宗", "Cyan Blade Sect", mode="substitute")

        assert r["ok"] and r["mode"] == "substitute"
        assert r["chapter_substitutions"] == 2
        assert r["chapters_affected"] == [5, 9]
        assert r["chapters_scanned"] is None
        assert _content(db, book, 5) == ["A disciple of the Cyan Blade Sect."]
        assert _content(db, book, 9) == ["The Cyan Blade Sect is famous."]
        assert epub_calls == [book]

    def test_safer_only_touches_chapters_whose_source_mentions_it(self, db, sect_book, epub_calls):
        book, _ = sect_book
        r = correct_entity(db, book, "青云宗", "Cyan Blade Sect", mode="safer")

        assert r["ok"]
        assert r["chapters_scanned"] == 1
        assert r["chapter_substitutions"] == 1
        assert r["chapters_affected"] == [5]
        assert _content(db, book, 5) == ["A disciple of the Cyan Blade Sect."]
        assert _content(db, book, 9) == ["The Azure Sword Sect is famous."]
        assert epub_calls == [book]

    def test_dry_run_writes_nothing_and_predicts_the_apply(self, db, sect_book, epub_calls):
        book, eid = sect_book
        _add_entity(db, book, "李四", "Li Si", origin_chapter=5,
                    note="An elder of the Azure Sword Sect.")

        dry = correct_entity(db, book, "青云宗", "Cyan Blade Sect",
                             mode="safer", dry_run=True)
        assert dry["ok"] and dry["dry_run"] and dry["status"] == "would_update"
        assert dry["changed"] is True
        assert (dry["chapter_substitutions"], dry["note_substitutions"]) == (1, 1)
        assert dry["chapters_affected"] == [5]
        # Nothing written: record, prose, notes, cache.
        assert db.get_entity_by_id(eid)["translation"] == "Azure Sword Sect"
        assert _content(db, book, 5) == ["A disciple of the Azure Sword Sect."]
        assert epub_calls == []

        real = correct_entity(db, book, "青云宗", "Cyan Blade Sect", mode="safer")
        assert (real["chapter_substitutions"], real["note_substitutions"]) == (1, 1)
        assert real["chapters_affected"] == dry["chapters_affected"]

    def test_dry_run_mode_none_counts_nothing(self, db, sect_book):
        book, eid = sect_book
        r = correct_entity(db, book, "青云宗", "Cyan Blade Sect", dry_run=True)
        assert r["ok"] and r["status"] == "would_update"
        assert r["chapter_substitutions"] == 0
        assert db.get_entity_by_id(eid)["translation"] == "Azure Sword Sect"

    def test_same_translation_is_a_no_op(self, db, sect_book, epub_calls):
        book, _ = sect_book
        r = correct_entity(db, book, "青云宗", "Azure Sword Sect", mode="substitute")
        assert r["ok"] and r["status"] == "unchanged" and r["changed"] is False
        assert r["chapter_substitutions"] == 0
        assert epub_calls == []

    def test_no_epub_invalidation_when_no_chapter_changed(self, db, book, epub_calls):
        _save_chapter(db, book, 1, ["无关。"], ["Nothing here."])
        _add_entity(db, book, "王五", "Wang Wu")
        epub_calls.clear()  # save_chapter invalidates on its own
        r = correct_entity(db, book, "王五", "Wang Five", mode="substitute")
        assert r["ok"] and r["chapter_substitutions"] == 0
        assert epub_calls == []

    def test_not_found(self, db, book):
        r = correct_entity(db, book, "不存在", "Nothing")
        assert r["ok"] is False and r["status"] == "not_found"
        assert "不存在" in r["error"]

    def test_category_filter_that_excludes_the_entity_is_not_found(self, db, sect_book):
        book, _ = sect_book
        r = correct_entity(db, book, "青云宗", "X", category="characters")
        assert r["status"] == "not_found" and "characters" in r["error"]

    def test_ambiguous_lists_categories(self, db, book, monkeypatch):
        monkeypatch.setattr(correct_entity_translation, "find_entity",
                            lambda *_: [(1, "characters", "Li"), (2, "places", "Li")])
        r = correct_entity(db, book, "李", "Lee")
        assert r["ok"] is False and r["status"] == "ambiguous"
        assert "characters" in r["error"] and "places" in r["error"]
        assert [m["category"] for m in r["matches"]] == ["characters", "places"]

        narrowed = correct_entity(db, book, "李", "Lee", category="places", dry_run=True)
        assert narrowed["ok"] and narrowed["entity_id"] == 2

    def test_invalid_mode(self, db, sect_book):
        book, eid = sect_book
        r = correct_entity(db, book, "青云宗", "Cyan Blade Sect", mode="everywhere")
        assert r["ok"] is False and r["status"] == "invalid_mode"
        assert db.get_entity_by_id(eid)["translation"] == "Azure Sword Sect"

    def test_empty_translation_refused(self, db, sect_book):
        book, _ = sect_book
        r = correct_entity(db, book, "青云宗", "  ")
        assert r["ok"] is False and r["status"] == "invalid_translation"


# --------------------------------------------------------------------------
# bulk_correct
# --------------------------------------------------------------------------

class TestBulkCorrect:
    def test_parse_correction(self):
        assert parse_correction("Lu") == ("Lu", None)
        assert parse_correction({"translation": "Lu", "category": "places"}) == ("Lu", "places")

    def test_cascade_applies_in_input_order(self, db, book):
        """B→C then A→B: the second sweep sees what the first left behind."""
        _save_chapter(db, book, 1, ["甲乙。"], ["Alpha met Beta."])
        _add_entity(db, book, "甲", "Alpha")
        _add_entity(db, book, "乙", "Beta")

        results = bulk_correct(db, book, {"乙": "Gamma", "甲": "Beta"}, mode="substitute")

        assert [r["status"] for r in results] == ["updated", "updated"]
        assert [r["untranslated"] for r in results] == ["乙", "甲"]
        assert _content(db, book, 1) == ["Beta met Gamma."]

    def test_reports_not_found_ambiguous_and_unchanged(self, db, book, monkeypatch):
        _add_entity(db, book, "甲", "Alpha")
        real_find = correct_entity_translation.find_entity

        def fake_find(dbm, book_id, key):
            if key == "李":
                return [(1, "characters", "Li"), (2, "places", "Li")]
            return real_find(dbm, book_id, key)

        monkeypatch.setattr(correct_entity_translation, "find_entity", fake_find)
        results = bulk_correct(db, book, {"甲": "Alpha", "无": "None", "李": "Lee"},
                               dry_run=True)
        assert [r["status"] for r in results] == ["unchanged", "not_found", "ambiguous"]

    def test_category_entry_passes_through(self, db, book):
        _add_entity(db, book, "甲", "Alpha", category="places")
        [r] = bulk_correct(db, book, {"甲": {"translation": "A", "category": "characters"}},
                           dry_run=True)
        assert r["status"] == "not_found"
        [r] = bulk_correct(db, book, {"甲": {"translation": "A", "category": "places"}},
                           dry_run=True)
        assert r["status"] == "would_update"

    def test_module_exports_generator(self):
        assert callable(bulk_correct_entities.iter_bulk_correct)


# --------------------------------------------------------------------------
# delete_entity
# --------------------------------------------------------------------------

class TestEntityDeletes:
    def test_plan_and_apply(self, db, book):
        a = _add_entity(db, book, "甲", "Alpha")
        _add_entity(db, book, "乙", "Beta")

        to_delete, errors = plan_entity_deletes(db, book, ["甲", "无"])
        assert to_delete == [{"id": a, "category": "characters",
                              "untranslated": "甲", "translation": "Alpha"}]
        assert len(errors) == 1 and "无" in errors[0]
        # Planning writes nothing.
        assert db.get_entity_by_id(a) is not None

        assert apply_entity_deletes(db, to_delete) == 1
        assert db.get_entity_by_id(a) is None
        # Deleting again finds nothing to delete.
        assert apply_entity_deletes(db, to_delete) == 0

    def test_ambiguous_key_is_refused(self, db, book, monkeypatch):
        monkeypatch.setattr(delete_entity, "find_entity",
                            lambda *_: [(1, "characters", "Li"), (2, "places", "Li")])
        to_delete, errors = plan_entity_deletes(db, book, ["李"])
        assert to_delete == []
        assert len(errors) == 1
        assert "characters" in errors[0] and "places" in errors[0]

    def test_category_narrows_an_ambiguous_key(self, db, book, monkeypatch):
        monkeypatch.setattr(delete_entity, "find_entity",
                            lambda *_: [(1, "characters", "Li"), (2, "places", "Li")])
        to_delete, errors = plan_entity_deletes(db, book, ["李"], category="places")
        assert errors == []
        assert [e["id"] for e in to_delete] == [2]

    def test_category_that_matches_nothing_is_an_error(self, db, book):
        _add_entity(db, book, "甲", "Alpha")
        to_delete, errors = plan_entity_deletes(db, book, ["甲"], category="places")
        assert to_delete == [] and "places" in errors[0]


# --------------------------------------------------------------------------
# change_entity_category
# --------------------------------------------------------------------------

class TestChangeEntityCategories:
    def test_dry_run_then_apply(self, db, book):
        a = _add_entity(db, book, "甲", "Alpha", category="characters")
        _add_entity(db, book, "乙", "Beta", category="places")

        dry = change_entity_categories(db, book, ["甲", "乙", "无"], "places", dry_run=True)
        assert dry["ok"] and dry["error"] is None
        assert [e["status"] for e in dry["results"]] == ["would_update", "unchanged", "not_found"]
        assert dry["counts"] == {"updated": 1, "unchanged": 1, "not_found": 1, "ambiguous": 0}
        assert db.get_entity_by_id(a)["category"] == "characters"

        real = change_entity_categories(db, book, ["甲"], "places")
        assert real["results"][0]["status"] == "updated"
        assert real["results"][0]["old_category"] == "characters"
        assert db.get_entity_by_id(a)["category"] == "places"

    def test_unknown_category_refused_unless_forced(self, db, book):
        a = _add_entity(db, book, "甲", "Alpha")

        refused = change_entity_categories(db, book, ["甲"], "made-up things")
        assert refused["ok"] is False and "made-up things" in refused["error"]
        assert refused["results"] == []
        assert db.get_entity_by_id(a)["category"] == "characters"

        forced = change_entity_categories(db, book, ["甲"], "made-up things", force=True)
        assert forced["ok"] and forced["results"][0]["status"] == "updated"
        assert db.get_entity_by_id(a)["category"] == "made-up things"

    def test_known_categories_include_book_categories(self, db, book):
        db.set_book_categories(book, ["characters", "cultivation realms"])
        assert "cultivation realms" in change_entity_category.known_categories(db, book)

    def test_ambiguous_key_reported(self, db, book, monkeypatch):
        monkeypatch.setattr(change_entity_category, "find_entities",
                            lambda *_: [(1, "characters", "Li"), (2, "places", "Li")])
        r = change_entity_categories(db, book, ["李"], "characters", dry_run=True)
        assert r["results"][0]["status"] == "ambiguous"
        assert r["results"][0]["categories"] == ["characters", "places"]

        narrowed = change_entity_categories(db, book, ["李"], "characters",
                                            current_category="places", dry_run=True)
        assert narrowed["results"][0]["status"] == "would_update"
        assert narrowed["results"][0]["entity_id"] == 2
