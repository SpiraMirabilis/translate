"""Tests for show_queue_item.py — printing a queued chapter's source."""
import json

import pytest

import show_queue_item


@pytest.fixture
def run_cli(db, monkeypatch):
    monkeypatch.setattr(show_queue_item, "TranslationConfig", lambda: db.config)
    monkeypatch.setattr(show_queue_item, "Logger", lambda cfg: db.logger)
    monkeypatch.setattr(show_queue_item, "DatabaseManager", lambda cfg, log, **kw: db)

    def run(*argv):
        monkeypatch.setattr("sys.argv", ["show_queue_item.py", *argv])
        return show_queue_item.main()

    return run


@pytest.fixture
def queued(db):
    book_id = db.create_book(title="Show Queue Book")
    queue_id = db.add_to_queue(book_id, ["第一章 剑雨", "张羽抬头看天。"],
                               title="第一章 剑雨", chapter_number=1)
    return book_id, queue_id


def test_prints_header_and_content(run_cli, queued, capsys):
    book_id, queue_id = queued
    assert run_cli("--book", str(book_id), "--chapter", "1") == 0

    out = capsys.readouterr().out
    assert f"Queue id:  {queue_id}" in out
    assert "Show Queue Book" in out
    assert "第一章 剑雨" in out
    assert "张羽抬头看天。" in out


def test_plain_omits_header(run_cli, queued, capsys):
    _, queue_id = queued
    assert run_cli("--queue-id", str(queue_id), "--plain") == 0

    out = capsys.readouterr().out
    assert "Queue id:" not in out
    assert out.splitlines()[0] == "第一章 剑雨"


def test_json_dumps_row(run_cli, queued, capsys):
    book_id, queue_id = queued
    assert run_cli("--queue-id", str(queue_id), "--json") == 0

    row = json.loads(capsys.readouterr().out)
    assert row["id"] == queue_id
    assert row["book_id"] == book_id
    assert row["chapter_number"] == 1
    assert "张羽抬头看天。" in row["content"]


def test_book_with_single_item_needs_no_chapter(run_cli, queued, capsys):
    book_id, _ = queued
    assert run_cli("--book", str(book_id), "--plain") == 0
    assert "第一章 剑雨" in capsys.readouterr().out


def test_requires_a_target(run_cli, queued):
    with pytest.raises(SystemExit, match="pass --book"):
        run_cli("--plain")


def test_unknown_chapter(run_cli, queued):
    book_id, _ = queued
    with pytest.raises(SystemExit, match="no queued chapter 42"):
        run_cli("--book", str(book_id), "--chapter", "42")
