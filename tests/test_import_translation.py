"""Tests for import_translation.py — importing a pre-made translation JSON
for a queued chapter, as if the model API had returned it.
"""
import json

import pytest

import import_translation
from conftest import FakeConfig, FakeLogger


@pytest.fixture
def run_cli(db, tmp_path, monkeypatch):
    """Invoke import_translation.main() against the tmp SQLite DB."""
    monkeypatch.setattr(import_translation, "TranslationConfig", lambda: db.config)
    monkeypatch.setattr(import_translation, "Logger", lambda cfg: db.logger)
    monkeypatch.setattr(import_translation, "DatabaseManager",
                        lambda cfg, log, **kw: db)

    def run(*argv):
        monkeypatch.setattr("sys.argv", ["import_translation.py", *argv])
        return import_translation.main()

    return run


@pytest.fixture
def payload_file(tmp_path):
    def write(payload, name="ch1.json"):
        path = tmp_path / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def queued_book(db):
    book_id = db.create_book(title="Import Test Book")
    queue_id = db.add_to_queue(
        book_id, ["第一章 剑雨", "张羽抬头看天。", "青云城下起了雨。"],
        title="第一章 剑雨", chapter_number=1,
    )
    assert queue_id
    return book_id, queue_id


PAYLOAD = {
    "title": "Chapter 1 - The Sword Rain",
    "chapter": 1,
    "summary": "Zhang Yu looks up as rain falls on Azure Cloud City.",
    "content": ["Zhang Yu looked up at the sky.", "", "Rain began to fall on Azure Cloud City."],
    "entities": {
        "characters": {"张羽": {"translation": "Zhang Yu", "gender": "male"}},
        "places": {"青云城": {"translation": "Azure Cloud City"}},
    },
}


class TestImport:
    def test_imports_chapter_and_clears_queue(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        assert run_cli("--book", str(book_id), "--chapter", "1",
                       "--file", payload_file(PAYLOAD), "--yes") == 0

        chapter = db.get_chapter(book_id=book_id, chapter_number=1)
        assert chapter is not None
        # "Chapter 1 - " prefix is stripped by the shared pipeline.
        assert chapter["title"] == "The Sword Rain"
        assert chapter["content"] == PAYLOAD["content"]
        # Source comes from the queue row (blank-line separated by the ingest).
        assert [l for l in chapter["untranslated"] if l.strip()] == [
            "第一章 剑雨", "张羽抬头看天。", "青云城下起了雨。"]
        assert chapter["summary"] == PAYLOAD["summary"]
        assert db.get_queue_count(book_id=book_id) == 0

    def test_persists_entities(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(PAYLOAD), "--yes")

        db._load_entities(book_id=book_id)
        with db._conn(dict_rows=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT category, untranslated, translation, gender, origin_chapter, last_chapter "
                "FROM entities WHERE book_id = ? ORDER BY untranslated", (book_id,))
            rows = [dict(r) for r in cur.fetchall()]

        by_key = {r["untranslated"]: r for r in rows}
        assert by_key["张羽"]["translation"] == "Zhang Yu"
        assert by_key["张羽"]["category"] == "characters"
        assert by_key["张羽"]["gender"] == "male"
        assert by_key["张羽"]["origin_chapter"] == 1
        assert int(by_key["张羽"]["last_chapter"]) == 1
        assert by_key["青云城"]["translation"] == "Azure Cloud City"

    def test_last_chapter_is_stamped_not_read_from_the_file(
            self, db, queued_book, payload_file, run_cli):
        """The field is code-owned everywhere, so a stale value in the file —
        the shape an old hand-written payload has — must not reach the row."""
        book_id, _ = queued_book
        stale = json.loads(json.dumps(PAYLOAD))
        stale["entities"]["characters"]["张羽"]["last_chapter"] = 3
        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(stale), "--yes")

        with db._conn(dict_rows=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_chapter FROM entities "
                        "WHERE book_id = ? AND untranslated = ?", (book_id, "张羽"))
            assert int(cur.fetchone()["last_chapter"]) == 1

    def test_dry_run_changes_nothing(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        assert run_cli("--book", str(book_id), "--chapter", "1",
                       "--file", payload_file(PAYLOAD), "--dry-run") == 0

        assert db.get_chapter(book_id=book_id, chapter_number=1) is None
        assert db.get_queue_count(book_id=book_id) == 1
        assert db.list_queue(book_id=book_id)[0]["status"] == "queued"

    def test_chapter_from_json_when_not_given(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        assert run_cli("--book", str(book_id), "--file", payload_file(PAYLOAD), "--yes") == 0
        assert db.get_chapter(book_id=book_id, chapter_number=1) is not None

    def test_queue_id_targeting(self, db, queued_book, payload_file, run_cli):
        book_id, queue_id = queued_book
        assert run_cli("--queue-id", str(queue_id), "--file", payload_file(PAYLOAD), "--yes") == 0
        assert db.get_chapter(book_id=book_id, chapter_number=1) is not None

    def test_keep_queue_leaves_item_queued(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(PAYLOAD), "--yes", "--keep-queue")

        assert db.get_chapter(book_id=book_id, chapter_number=1) is not None
        queue = db.list_queue(book_id=book_id, include_processing=True)
        assert len(queue) == 1
        assert queue[0]["status"] == "queued"

    def test_draft_saves_unpublished(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(PAYLOAD), "--yes", "--draft")

        assert db.get_chapter(book_id=book_id, chapter_number=1)["published_at"] is None

    def test_queue_chapter_number_wins_over_json(self, db, payload_file, run_cli):
        book_id = db.create_book(title="Mismatch Book")
        db.add_to_queue(book_id, ["第七章", "内容"], title="第七章", chapter_number=7)

        run_cli("--book", str(book_id), "--chapter", "7",
                "--file", payload_file(PAYLOAD), "--yes")

        assert db.get_chapter(book_id=book_id, chapter_number=7) is not None
        assert db.get_chapter(book_id=book_id, chapter_number=1) is None


class TestValidation:
    def test_rejects_missing_content(self, tmp_path, payload_file, queued_book, run_cli):
        book_id, _ = queued_book
        bad = {k: v for k, v in PAYLOAD.items() if k != "content"}
        with pytest.raises(SystemExit, match="content"):
            run_cli("--book", str(book_id), "--file", payload_file(bad), "--yes")

    def test_rejects_entity_without_translation(self, payload_file, queued_book, run_cli):
        book_id, _ = queued_book
        bad = dict(PAYLOAD, entities={"characters": {"张羽": {"gender": "male"}}})
        with pytest.raises(SystemExit, match="translation"):
            run_cli("--book", str(book_id), "--file", payload_file(bad), "--yes")

    def test_rejects_unknown_chapter(self, payload_file, queued_book, run_cli):
        book_id, _ = queued_book
        with pytest.raises(SystemExit, match="no queued chapter 99"):
            run_cli("--book", str(book_id), "--chapter", "99",
                    "--file", payload_file(PAYLOAD), "--yes")

    def test_rejects_non_json(self, tmp_path, queued_book, run_cli):
        book_id, _ = queued_book
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="not valid JSON"):
            run_cli("--book", str(book_id), "--file", str(path), "--yes")


class TestOverwriteGuard:
    def test_refuses_existing_chapter_without_confirmation(self, db, queued_book,
                                                           payload_file, run_cli, monkeypatch):
        book_id, _ = queued_book
        db.save_chapter(book_id, 1, "Existing", ["原文"], ["Existing translation"])

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with pytest.raises(SystemExit, match="Overwrite existing chapter 1"):
            run_cli("--book", str(book_id), "--chapter", "1", "--file", payload_file(PAYLOAD))

        assert db.get_chapter(book_id=book_id, chapter_number=1)["content"] == ["Existing translation"]
        assert db.get_queue_count(book_id=book_id) == 1

    def test_overwrite_flag_replaces_chapter(self, db, queued_book, payload_file, run_cli):
        book_id, _ = queued_book
        db.save_chapter(book_id, 1, "Existing", ["原文"], ["Existing translation"])

        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(PAYLOAD), "--overwrite", "--yes")

        assert db.get_chapter(book_id=book_id, chapter_number=1)["content"] == PAYLOAD["content"]


class TestIllustrationMarkers:
    def test_reasserts_dropped_marker(self, db, payload_file, run_cli):
        book_id = db.create_book(title="Illustrated Book")
        db.add_to_queue(book_id, ["第一章", "⟦IMG:abc123⟧", "内容"],
                        title="第一章", chapter_number=1)

        payload = dict(PAYLOAD, content=["Chapter one text."], entities={})
        run_cli("--book", str(book_id), "--chapter", "1",
                "--file", payload_file(payload), "--yes")

        content = db.get_chapter(book_id=book_id, chapter_number=1)["content"]
        assert "⟦IMG:abc123⟧" in content
