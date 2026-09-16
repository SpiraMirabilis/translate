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


class TestSubstituteInEntityNotes:
    """A rename must not leave the old term stranded in entity notes: notes are
    fed back into later translations, so a stale one re-seeds the corrected term.
    """

    def test_scoped_to_origin_chapter(self, db, book):
        inside = _add_entity(db, untranslated="李四", translation="Li Si", book_id=book,
                             origin_chapter=5, note="Elder of the Azure Sword Sect.")
        outside = _add_entity(db, untranslated="王五", translation="Wang Wu", book_id=book,
                              origin_chapter=9, note="Rival from the Azure Sword Sect.")

        changed = db.substitute_in_entity_notes(
            book, "Azure Sword Sect", "Cyan Blade Sect", chapter_numbers={5},
        )

        assert changed == 1
        assert db.get_entity_by_id(inside)["note"] == "Elder of the Cyan Blade Sect."
        assert db.get_entity_by_id(outside)["note"] == "Rival from the Azure Sword Sect."

    def test_book_wide_sweep_takes_every_note(self, db, book):
        """chapter_numbers=None mirrors a book-wide prose sweep — including
        entities whose origin_chapter is NULL, which no chapter set can match."""
        no_origin = _add_entity(db, untranslated="王五", translation="Wang Wu", book_id=book,
                                note="Guards the Azure Sword Sect gate.")
        far = _add_entity(db, untranslated="李四", translation="Li Si", book_id=book,
                          origin_chapter=900, note="Azure Sword Sect elder.")

        changed = db.substitute_in_entity_notes(book, "Azure Sword Sect", "Cyan Blade Sect")

        assert changed == 2
        assert "Cyan Blade Sect" in db.get_entity_by_id(no_origin)["note"]
        assert "Cyan Blade Sect" in db.get_entity_by_id(far)["note"]

    def test_leaves_other_books_and_globals_alone(self, db, book):
        other_book = db.create_book(title="Other Book")
        other = _add_entity(db, untranslated="李四", translation="Li Si", book_id=other_book,
                            origin_chapter=5, note="Azure Sword Sect elder.")
        glob = _add_entity(db, untranslated="王五", translation="Wang Wu", book_id=None,
                           origin_chapter=5, note="Azure Sword Sect founder.")

        assert db.substitute_in_entity_notes(book, "Azure Sword Sect", "Cyan Blade Sect") == 0
        assert db.get_entity_by_id(other)["note"] == "Azure Sword Sect elder."
        assert db.get_entity_by_id(glob)["note"] == "Azure Sword Sect founder."

    def test_dry_run_counts_without_writing(self, db, book):
        eid = _add_entity(db, untranslated="李四", translation="Li Si", book_id=book,
                          origin_chapter=5, note="Azure Sword Sect elder.")

        assert db.substitute_in_entity_notes(
            book, "Azure Sword Sect", "Cyan Blade Sect", dry_run=True) == 1
        assert db.get_entity_by_id(eid)["note"] == "Azure Sword Sect elder."

    def test_word_boundary_and_case_preservation(self, db, book):
        """Same chapter_text_ops semantics as the prose sweep: positional casing
        is preserved, and -w fences the match to whole words."""
        eid = _add_entity(db, untranslated="李四", translation="Li Si", book_id=book,
                          origin_chapter=5, note="Dai leads them; Daiyu does not.")

        changed = db.substitute_in_entity_notes(
            book, "Dai", "Tai", chapter_numbers={5}, word_boundary=True,
        )

        assert changed == 1
        assert db.get_entity_by_id(eid)["note"] == "Tai leads them; Daiyu does not."

    def test_noop_when_translation_unchanged(self, db, book):
        _add_entity(db, untranslated="李四", translation="Li Si", book_id=book,
                    origin_chapter=5, note="Azure Sword Sect elder.")

        assert db.substitute_in_entity_notes(book, "Azure Sword Sect", "Azure Sword Sect") == 0
        assert db.substitute_in_entity_notes(book, "", "Cyan Blade Sect") == 0


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

    def test_claim_stamps_host_and_pid_by_default(self, db, book):
        import os
        from db.queue_repo import worker_identity

        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        item = db.claim_next_queue_item()
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT claimed_by FROM queue WHERE id = ?", (item["id"],))
            claimed_by = cur.fetchone()[0]
        assert claimed_by == worker_identity()
        assert claimed_by.endswith(f":{os.getpid()}")


class TestDeadWorkerClaims:
    """A restart during the two-pass review pause used to strand the chapter:
    the row stayed 'processing' forever, invisible to list_queue and unclaimable."""

    def _set_owner(self, db, queue_id, owner):
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE queue SET claimed_by = ? WHERE id = ?", (owner, queue_id))

    def test_dead_pid_claim_is_requeued(self, db, book):
        import socket

        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        item = db.claim_next_queue_item()
        assert db.get_queue_count() == 0
        # Worker died holding the claim (PID 2**22 is above /proc/sys/kernel/pid_max).
        self._set_owner(db, item["id"], f"{socket.gethostname()}:{2**22}")

        assert db.release_dead_worker_claims() == 1
        assert db.get_queue_count() == 1
        assert db.claim_next_queue_item()["id"] == item["id"]

    def test_live_worker_claim_is_left_alone(self, db, book):
        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        item = db.claim_next_queue_item()  # stamped with this live PID

        assert db.release_dead_worker_claims() == 0
        assert db.get_queue_count() == 0
        assert db.claim_next_queue_item() is None
        assert item is not None

    def test_foreign_host_and_legacy_claims_are_left_alone(self, db, book):
        # The public app booting must not reclaim a row the admin app is
        # translating; a pre-upgrade 'worker' stamp carries no PID to check.
        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        db.add_to_queue(book, ["b"], title="2", chapter_number=2)
        first = db.claim_next_queue_item()
        second = db.claim_next_queue_item()
        self._set_owner(db, first["id"], "some-other-host:1234")
        self._set_owner(db, second["id"], "worker")

        assert db.release_dead_worker_claims() == 0
        assert db.get_queue_count() == 0

    def test_list_queue_can_surface_in_flight_rows(self, db, book):
        db.add_to_queue(book, ["a"], title="1", chapter_number=1)
        db.add_to_queue(book, ["b"], title="2", chapter_number=2)
        claimed = db.claim_next_queue_item()

        assert [i["id"] for i in db.list_queue()] != [claimed["id"]]
        assert claimed["id"] not in {i["id"] for i in db.list_queue()}

        rows = db.list_queue(include_processing=True)
        by_id = {i["id"]: i for i in rows}
        assert len(rows) == 2
        assert by_id[claimed["id"]]["status"] == "processing"
        assert by_id[claimed["id"]]["claimed_at"]
        other = next(i for i in rows if i["id"] != claimed["id"])
        assert other["status"] == "queued"
