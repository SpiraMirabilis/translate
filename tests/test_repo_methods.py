"""Tests for the additive B4 repo methods on DatabaseManager mixins:
ID-based entity accessors (db/entities_repo.py) and the proofread
timestamp methods (db/chapters_repo.py).
"""
import pytest


def _add_entity(db, category="characters", untranslated="张羽", translation="Zhang Yu",
                book_id=None, **kw):
    assert db.add_entity(category, untranslated, translation, book_id=book_id, **kw)
    ent = None
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM entities WHERE category = ? AND untranslated = ?",
            (category, untranslated),
        )
        ent = cur.fetchone()
    return ent["id"]


@pytest.fixture
def book(db):
    book_id = db.create_book(title="Repo Test Book")
    assert book_id
    return book_id


class TestEntityById:
    def test_get_entity_by_id(self, db, book):
        eid = _add_entity(db, book_id=book, gender="male", note="the MC")
        ent = db.get_entity_by_id(eid)
        assert ent["id"] == eid
        assert ent["category"] == "characters"
        assert ent["untranslated"] == "张羽"
        assert ent["translation"] == "Zhang Yu"
        assert ent["gender"] == "male"
        assert ent["note"] == "the MC"
        assert ent["book_id"] == book

    def test_get_entity_by_id_missing(self, db):
        assert db.get_entity_by_id(999999) is None

    def test_update_entity_by_id(self, db, book):
        eid = _add_entity(db, book_id=book)
        assert db.update_entity_by_id(
            eid, translation="Zhang Feather", incorrect_translation="Zhang Yu"
        )
        ent = db.get_entity_by_id(eid)
        assert ent["translation"] == "Zhang Feather"
        assert ent["incorrect_translation"] == "Zhang Yu"

    def test_update_entity_by_id_book_id_none_moves_to_global(self, db, book):
        eid = _add_entity(db, book_id=book)
        assert db.update_entity_by_id(eid, book_id=None)
        assert db.get_entity_by_id(eid)["book_id"] is None

    def test_update_entity_by_id_rejects_unknown_column(self, db, book):
        eid = _add_entity(db, book_id=book)
        with pytest.raises(ValueError):
            db.update_entity_by_id(eid, bogus_column="x")

    def test_update_entity_by_id_no_fields(self, db, book):
        eid = _add_entity(db, book_id=book)
        assert db.update_entity_by_id(eid) is False

    def test_delete_entity_by_id(self, db, book):
        eid = _add_entity(db, book_id=book)
        assert db.delete_entity_by_id(eid) is True
        assert db.get_entity_by_id(eid) is None
        assert db.delete_entity_by_id(eid) is False


class TestListGenderedEntities:
    def test_filters_by_gender_translation_and_category(self, db, book):
        _add_entity(db, untranslated="张羽", translation="Zhang Yu",
                    book_id=book, gender="male")
        _add_entity(db, untranslated="李四", translation="Li Si", book_id=book)  # no gender
        _add_entity(db, category="places", untranslated="青云山",
                    translation="Azure Cloud Mountain", book_id=book, gender="female")

        rows = db.list_gendered_entities(book, ["characters"])
        assert [(r["translation"], r["gender"]) for r in rows] == [("Zhang Yu", "male")]

        # places included when its category is requested
        rows = db.list_gendered_entities(book, ["characters", "places"])
        assert {r["translation"] for r in rows} == {"Zhang Yu", "Azure Cloud Mountain"}

    def test_empty_categories_defaults_to_characters(self, db, book):
        _add_entity(db, book_id=book, gender="neutral")
        rows = db.list_gendered_entities(book, [])
        assert len(rows) == 1
        assert rows[0]["gender"] == "neutral"


class TestCountEntitiesByCategory:
    def test_counts_include_global(self, db, book):
        _add_entity(db, untranslated="张羽", translation="Zhang Yu", book_id=book)
        _add_entity(db, untranslated="李四", translation="Li Si", book_id=book)
        _add_entity(db, category="places", untranslated="青云山",
                    translation="Azure Cloud Mountain", book_id=book)
        _add_entity(db, untranslated="王五", translation="Wang Wu", book_id=None)  # global

        counts = db.count_entities_by_category(book)
        assert counts == {"characters": 3, "places": 1}


class TestChapterProofread:
    def _save(self, db, book, num):
        assert db.save_chapter(
            book_id=book, chapter_number=num, title=f"Ch {num}",
            untranslated_content=[f"原文{num}"], translated_content=[f"line {num}"],
        )

    def test_set_and_clear(self, db, book):
        self._save(db, book, 1)
        now = db.set_chapter_proofread(book, 1, True)
        # SQLite backend → ISO-8601 with Z suffix
        assert now and now.endswith("Z") and "T" in now
        assert db.set_chapter_proofread(book, 1, False) is None

    def test_missing_chapter_raises_lookup_error(self, db, book):
        with pytest.raises(LookupError):
            db.set_chapter_proofread(book, 42, True)

    def test_bulk_counts_only_existing(self, db, book):
        self._save(db, book, 1)
        self._save(db, book, 2)
        updated, now = db.set_chapters_proofread(book, [1, 2, 99], True)
        assert updated == 2
        assert now and now.endswith("Z")
        updated, now = db.set_chapters_proofread(book, [1], False)
        assert updated == 1
        assert now is None


class TestQueueClaim:
    def test_claim_is_exclusive_and_releasable(self, db, book):
        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        db.add_to_queue(book, ["b"], title="2", chapter_number=2)
        first = db.claim_next_queue_item(worker_id="t1")
        second = db.claim_next_queue_item(worker_id="t2")
        assert first["chapter_number"] == 1
        assert second["chapter_number"] == 2
        assert db.claim_next_queue_item() is None
        assert db.get_queue_count() == 0
        assert db.release_queue_item(first["id"]) is True
        assert db.get_queue_count() == 1
        reclaimed = db.claim_next_queue_item(worker_id="t3")
        assert reclaimed["id"] == first["id"]
