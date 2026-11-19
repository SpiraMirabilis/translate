"""Flat-text novels: queue_flat_text's parser and the single-.txt web upload.

Two header styles: 第N章 标题, and (only when a file has none of those) bare
numbered headers at column 0 — ``001 标题`` / ``001: 标题`` / ``001：标题``.
"""
from queue_flat_text import parse_chapters_text

NUMERIC = (
    "賽博朋克\n作者：某\n簡介：\n　　一段簡介。\n\n"
    "001 夜之城？\n　　正文一。\n2077 年的事。\n"
    "002: 公司狗\n　　正文二。\n"
    "004：鐵霸王之死\n　　正文四。\n"
)


def test_numeric_headers():
    chapters, volumes = parse_chapters_text(NUMERIC)
    assert [c["src_number"] for c in chapters] == [1, 2, 4]
    assert [c["title"] for c in chapters] == ["夜之城？", "公司狗", "鐵霸王之死"]
    # The blurb is dropped; a body line opening with a year stays prose
    # (2077 is far past the previous header's 1).
    assert chapters[0]["content_lines"] == ["　　正文一。", "2077 年的事。"]
    assert len(volumes) == 1


def test_numeric_headers_ignored_when_the_file_has_zhang_headers():
    text = "第1章 甲\n　　正文\n002 不是标题\n第2章 乙\n　　正文\n"
    chapters, _ = parse_chapters_text(text)
    assert [c["title"] for c in chapters] == ["甲", "乙"]
    assert "002 不是标题" in chapters[0]["content_lines"]


def test_indented_numbers_are_prose():
    chapters, _ = parse_chapters_text("001 甲\n　　1. 任务一\n　　2. 任务二\n002 乙\n　　x\n")
    assert [c["src_number"] for c in chapters] == [1, 2]
    assert len(chapters[0]["content_lines"]) == 2


def _upload(client, book_id, text, **form):
    data = {"book_id": str(book_id), **{k: str(v) for k, v in form.items()}}
    return client.post("/api/queue/upload", data=data,
                       files={"file": ("nightcity.txt", text.encode("utf-8"), "text/plain")})


def _queued(client, book_id):
    items = client.get(f"/api/queue?book_id={book_id}").json()["items"]
    return sorted((i["chapter_number"], i["title"]) for i in items)


def test_upload_splits_a_flat_file(admin_client, db):
    book_id = db.create_book("Night City")
    r = _upload(admin_client, book_id, NUMERIC)
    assert r.status_code == 200, r.text
    assert r.json()["chapters_added"] == 3
    # Sequential from the first header, so the source's skipped 003 closes up.
    assert _queued(admin_client, book_id) == [(1, "夜之城？"), (2, "公司狗"), (3, "鐵霸王之死")]


def test_upload_start_number_and_conflicts(admin_client, db):
    book_id = db.create_book("Night City")
    db.add_to_queue(book_id, ["已有"], title="existing", chapter_number=11)

    r = _upload(admin_client, book_id, NUMERIC, chapter_number=10)
    assert r.json()["status"] == "conflict"
    assert r.json()["existing_numbers"] == [11]

    r = _upload(admin_client, book_id, NUMERIC, chapter_number=10, on_conflict="discard")
    assert r.json()["chapters_added"] == 2 and r.json()["skipped"] == 1
    assert _queued(admin_client, book_id) == [
        (10, "夜之城？"), (11, "existing"), (12, "鐵霸王之死")]


def test_upload_single_chapter_unchanged(admin_client, db):
    book_id = db.create_book("One")
    r = _upload(admin_client, book_id, "第1章 甲\n　　正文\n")
    assert r.status_code == 200, r.text
    assert "queue_id" in r.json()
    assert len(_queued(admin_client, book_id)) == 1
