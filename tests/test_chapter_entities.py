"""Per-chapter entity index (chapter_entities) and the reader's terms endpoint.

Covers what the "Terms this chapter" panel depends on: that save_chapter keeps
the index current, that only terms actually present in the chapter are listed,
that notes are the notes of that chapter rather than the current ones, and that
an unpublished chapter's glossary is not public.
"""
import pytest


def _seed_book(db, title="Terms Book"):
    book_id = db.create_book(title)
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id,
                  origin_chapter=1, gender="male", note="Protagonist at ch1.",
                  note_author="model", note_chapter=1)
    db.add_entity("places", "青云宗", "Azure Cloud Sect", book_id=book_id,
                  origin_chapter=1)
    db.add_entity("creatures", "火蛟", "Flame Dragon", book_id=book_id,
                  origin_chapter=7)
    return book_id


def _save(db, book_id, num, source, **kw):
    return db.save_chapter(
        book_id=book_id, chapter_number=num, title=f"Chapter {num}",
        untranslated_content=source,
        translated_content=[f"Translated line {num}"], **kw)


class TestIndexing:
    def test_save_chapter_indexes_present_entities_only(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元走进青云宗。", "陈元笑了。"])

        terms = db.get_chapter_terms(book_id, 1)
        by_key = {t["untranslated"]: t for t in terms}
        assert set(by_key) == {"陈元", "青云宗"}          # 火蛟 is not in the text
        assert by_key["陈元"]["occurrences"] == 2
        assert by_key["青云宗"]["occurrences"] == 1

    def test_resave_replaces_rows(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元走进青云宗。"])
        _save(db, book_id, 1, ["陈元一个人。"])           # retranslation, sect gone

        assert {t["untranslated"] for t in db.get_chapter_terms(book_id, 1)} == {"陈元"}

    def test_reindex_picks_up_an_entity_added_later(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元进入试炼塔。"])
        assert {t["untranslated"] for t in db.get_chapter_terms(book_id, 1)} == {"陈元"}

        # The glossary moves on after the chapter was written — the index is
        # only refreshed by a reindex, which is exactly what
        # backfill_chapter_entities.py is for.
        db.add_entity("places", "试炼塔", "Trial Tower", book_id=book_id, origin_chapter=9)
        assert {t["untranslated"] for t in db.get_chapter_terms(book_id, 1)} == {"陈元"}

        done, rows = db.reindex_book_chapter_entities(book_id)
        assert done == 1 and rows == 2
        assert {t["untranslated"] for t in db.get_chapter_terms(book_id, 1)} == {"陈元", "试炼塔"}

    def test_only_missing_skips_indexed_chapters(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元。"])
        _save(db, book_id, 2, ["青云宗。"])

        done, _ = db.reindex_book_chapter_entities(book_id, only_missing=True)
        assert done == 0

    def test_global_entities_are_indexed_too(self, db):
        book_id = _seed_book(db)
        db.add_entity("titles", "真人", "True Master", book_id=None)
        _save(db, book_id, 1, ["陈元真人。"])

        assert "真人" in {t["untranslated"] for t in db.get_chapter_terms(book_id, 1)}


class TestTermPayload:
    def test_gender_only_for_gendered_categories(self, db):
        book_id = _seed_book(db)
        # Give the place a gender value it has no business showing.
        db.update_entity("places", "青云宗", gender="female", book_id=book_id)
        _save(db, book_id, 1, ["陈元走进青云宗。"])

        by_key = {t["untranslated"]: t for t in db.get_chapter_terms(book_id, 1)}
        assert by_key["陈元"]["gender"] == "male"        # characters is gendered
        assert "gender" not in by_key["青云宗"]           # places is not

    def test_first_seen_flags_the_introducing_chapter(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元。"])
        _save(db, book_id, 2, ["陈元。"])

        assert db.get_chapter_terms(book_id, 1)[0]["first_seen"] is True
        assert db.get_chapter_terms(book_id, 2)[0]["first_seen"] is False

    def test_note_is_the_note_of_that_chapter(self, db):
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元。"])
        _save(db, book_id, 9, ["陈元。"])

        entity_id = db.get_entity_id(book_id, "陈元")
        db.set_entity_note(entity_id, "Broke through to Golden Core in ch8.",
                           author="model", chapter_number=8)

        early = db.get_chapter_terms(book_id, 1)[0]
        late = db.get_chapter_terms(book_id, 9)[0]
        assert early["note"] == "Protagonist at ch1."     # not the ch8 fact
        assert late["note"] == "Broke through to Golden Core in ch8."

    def test_note_changed_flags_the_chapter_that_rewrote_it(self, db):
        book_id = _seed_book(db)
        for n in (1, 8, 9):
            _save(db, book_id, n, ["陈元。"])

        entity_id = db.get_entity_id(book_id, "陈元")
        db.set_entity_note(entity_id, "Broke through to Golden Core in ch8.",
                           author="model", chapter_number=8)

        assert db.get_chapter_terms(book_id, 8)[0]["note_changed"] is True
        assert db.get_chapter_terms(book_id, 9)[0]["note_changed"] is False
        assert db.get_chapter_terms(book_id, 1)[0]["note_changed"] is False

    def test_note_creation_is_not_a_change(self, db):
        """The seeded note was written at ch1 — the chip must not double up
        with "new" on the chapter that introduced the entity."""
        book_id = _seed_book(db)
        _save(db, book_id, 1, ["陈元。"])

        term = db.get_chapter_terms(book_id, 1)[0]
        assert term["first_seen"] is True
        assert term["note_changed"] is False

    def test_undated_note_edits_belong_to_the_present(self, db):
        """A hand edit or script sweep carries no chapter, so it flags none."""
        book_id = _seed_book(db)
        _save(db, book_id, 4, ["陈元。"])

        entity_id = db.get_entity_id(book_id, "陈元")
        db.set_entity_note(entity_id, "Corrected by hand.", author="human")

        assert db.get_chapter_terms(book_id, 4)[0]["note_changed"] is False

    def test_unindexed_chapter_returns_empty_not_error(self, db):
        book_id = _seed_book(db)
        assert db.get_chapter_terms(book_id, 404) == []


class TestPublishingGate:
    def test_draft_chapter_terms_are_not_public(self, db):
        book_id = db.create_book("Draft Terms Book", is_original=True)
        db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, origin_chapter=1)
        _save(db, book_id, 1, ["陈元。"])                  # original work → draft

        assert db.get_chapter_terms(book_id, 1)                       # admin sees it
        assert db.get_chapter_terms(book_id, 1, published_only=True) == []


@pytest.fixture(autouse=True)
def reset_public_limiter():
    from web.api import public as public_api

    public_api._public_limiter.reset()
    yield
    public_api._public_limiter.reset()


@pytest.fixture
def public_client(web_app):
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(web_app, headers={"Origin": "http://testserver"})


class TestTermsEndpoint:
    def test_public_endpoint_returns_the_chapter_glossary(self, db, public_client):
        book_id = _seed_book(db, title="Public Terms Book")
        _save(db, book_id, 1, ["陈元走进青云宗。"])

        resp = public_client.get(f"/api/public/books/{book_id}/chapters/1/terms")
        assert resp.status_code == 200, resp.text
        terms = resp.json()["terms"]
        assert {t["untranslated"] for t in terms} == {"陈元", "青云宗"}
        assert terms[0]["translation"] == "Chen Yuan"

    def test_admin_endpoint_requires_a_session(self, db, public_client):
        book_id = _seed_book(db, title="Admin Terms Book")
        _save(db, book_id, 1, ["陈元。"])

        assert public_client.get(
            f"/api/books/{book_id}/chapters/1/terms").status_code == 401
