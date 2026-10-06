"""The T9 MCP server (mcp_server/): tool inventory, the idle-book guard, and
each tool family against a throwaway SQLite DB. The admin server is faked —
no test here makes an HTTP request or a model call.
"""
import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server.server import build_server


class FakeAdmin:
    """Stands in for deps.AdminClient: a settable status payload, recorded calls."""

    def __init__(self):
        self.jobs = {}
        self.calls = []
        self.statuses = None       # optional list of payloads served in order
        self.fail = None
        self.process_next_error = None

    def status(self):
        self.calls.append(("status",))
        if self.fail:
            raise self.fail
        if self.statuses:
            payload = self.statuses.pop(0)
            self.jobs = payload.get("jobs", {})
            return payload
        return {"status": "running" if self.jobs else "idle", "jobs": self.jobs}

    def stop_auto(self, book_id):
        self.calls.append(("stop_auto", book_id))
        return {"status": "stopping", "stopped": [book_id]}

    def process_next(self, payload):
        self.calls.append(("process_next", payload))
        if self.process_next_error:
            raise self.process_next_error
        return {"status": "started"}


def running_job(book_id, **kw):
    job = {"book_id": book_id, "status": "running", "is_running": True,
           "chapter_number": 12, "auto_process": True, "auto_remaining": 3,
           "run_options": {"translation_model": "claude:x", "no_review": True,
                           "auto_process": True, "max_chapters": 5}}
    job.update(kw)
    return job


@pytest.fixture(autouse=True)
def usage_log(tmp_path, monkeypatch):
    """Keep tool-call logging out of the real logs/mcp_usage.log."""
    path = tmp_path / "mcp_usage.log"
    monkeypatch.setenv("MCP_USAGE_LOG", str(path))
    return path


@pytest.fixture
def admin():
    return FakeAdmin()


@pytest.fixture
def server(db, admin):
    return build_server(db=db, admin=admin)


def call(server, name, **args):
    """Invoke a tool and return its text."""
    result = asyncio.run(server.call_tool(name, args))
    return result[0].text


def call_json(server, name, **args):
    return json.loads(call(server, name, **args))


@pytest.fixture
def book(db):
    book_id = db.create_book(title="MCP Test Book")
    assert book_id
    return book_id


def add_entity(db, untranslated, translation, book_id, category="characters", **kw):
    assert db.add_entity(category, untranslated, translation, book_id=book_id, **kw)
    return db.get_entity_id(book_id, untranslated, category)


def save_chapter(db, book_id, number, source, translated, title=None):
    assert db.save_chapter(book_id=book_id, chapter_number=number,
                           title=title or f"Chapter {number}",
                           untranslated_content=source, translated_content=translated)


# ── inventory ────────────────────────────────────────────────────────────────

def tools_by_name(server):
    return {t.name: t for t in asyncio.run(server.list_tools())}


def test_every_tool_is_prefixed_and_annotated(server):
    tools = tools_by_name(server)
    assert tools
    for name, t in tools.items():
        assert name.startswith("t9_")
        assert t.annotations is not None and t.annotations.readOnlyHint is not None, name
        assert t.description, name


def test_read_only_server_exposes_only_getters(db, admin):
    full = tools_by_name(build_server(db=db, admin=admin))
    ro = tools_by_name(build_server(db=db, admin=admin, read_only=True))
    assert set(ro) == {n for n, t in full.items() if t.annotations.readOnlyHint}
    assert "t9_set_entity_note" not in ro
    assert "t9_list_books" in ro


def test_read_only_server_refuses_write_tools(db, admin):
    ro = build_server(db=db, admin=admin, read_only=True)
    with pytest.raises(ToolError):
        call(ro, "t9_set_entity_note", book_id=1, untranslated="x", note="y")


def test_tools_write_nothing_to_stdout(server, db, book, capsys):
    save_chapter(db, book, 1, ["第一章", "陈元来了。"], ["Chapter 1", "Chen Yuan came."])
    call(server, "t9_list_books")
    call(server, "t9_get_chapter", book_id=book, chapter_number=1)
    call(server, "t9_search_chapters", book_id=book, query="Chen")
    assert capsys.readouterr().out == ""


# ── books ────────────────────────────────────────────────────────────────────

def test_list_and_get_book(server, db, book):
    save_chapter(db, book, 1, ["第一章"], ["Chapter 1"])
    assert "MCP Test Book" in call(server, "t9_list_books", query="mcp test")
    assert call(server, "t9_list_books", query="nope") == "No books match."
    info = call_json(server, "t9_get_book", book_id=book)
    assert info["progress"]["translated"] == 1
    assert "characters" in info["categories"]


def test_get_book_unknown_id(server):
    with pytest.raises(ToolError, match="No book"):
        call(server, "t9_get_book", book_id=9999)


def test_get_chapter_sides_and_paging(server, db, book):
    save_chapter(db, book, 3, ["第三章", "甲。", "乙。"], ["Chapter 3", "A.", "B."])
    text = call(server, "t9_get_chapter", book_id=book, chapter_number=3,
                side="both", line_start=1, line_count=1)
    assert "[1] A." in text and "pass line_start=2" in text
    assert "甲。" in call(server, "t9_get_chapter", book_id=book, chapter_number=3, side="src")


def test_get_chapter_summaries_filter_and_paging(server, db, book):
    for n in (1, 2, 3, 10):
        assert db.save_chapter(book_id=book, chapter_number=n, title=f"Title {n}",
                               untranslated_content=["第章"], translated_content=["Ch"],
                               summary=f"Summary of chapter {n}.")
    text = call(server, "t9_get_chapter_summaries", book_id=book, chapters="2-3,10")
    assert "ch2: Title 2\nSummary of chapter 2." in text
    assert "ch10: Title 10" in text and "chapter 1." not in text
    data = call_json(server, "t9_get_chapter_summaries", book_id=book, limit=2,
                     format="json")
    assert [c["chapter"] for c in data["chapters"]] == [1, 2]
    assert data["has_more"] and data["next_offset"] == 2
    assert "no stored chapters matching" in call(
        server, "t9_get_chapter_summaries", book_id=book, chapters=">100")
    with pytest.raises(ToolError, match="Bad chapters spec"):
        call(server, "t9_get_chapter_summaries", book_id=book, chapters="x")


def test_get_chapter_summaries_on_read_only_server(db, admin, book):
    ro = build_server(db=db, admin=admin, read_only=True)
    assert "t9_get_chapter_summaries" in tools_by_name(ro)


# ── guard ────────────────────────────────────────────────────────────────────

def test_guard_refuses_while_book_translates(server, db, admin, book):
    add_entity(db, "陈元", "Chen Yuan", book)
    admin.jobs = {str(book): running_job(book)}
    with pytest.raises(ToolError, match="t9_pause_translation"):
        call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="A cultivator.")
    assert db.get_entity_by_id(db.get_entity_id(book, "陈元"))["note"] is None


def test_guard_force_overrides(server, db, admin, book):
    eid = add_entity(db, "陈元", "Chen Yuan", book)
    admin.jobs = {str(book): running_job(book)}
    out = call_json(server, "t9_set_entity_note", book_id=book, untranslated="陈元",
                    note="A cultivator.", force=True)
    assert out["changed"]
    assert db.get_entity_by_id(eid)["note"] == "A cultivator."


def test_guard_other_book_running_passes(server, db, admin, book):
    eid = add_entity(db, "陈元", "Chen Yuan", book)
    admin.jobs = {str(book + 1): running_job(book + 1)}
    call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="Note.")
    assert db.get_entity_by_id(eid)["note"] == "Note."


def test_guard_refuses_when_admin_unreachable(server, db, admin, book):
    from mcp_server.deps import AdminUnreachable
    add_entity(db, "陈元", "Chen Yuan", book)
    admin.fail = AdminUnreachable("down")
    with pytest.raises(ToolError, match="Cannot verify"):
        call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="Note.")


def test_guard_sees_cli_processing_claims(server, db, book):
    add_entity(db, "陈元", "Chen Yuan", book)
    db.add_to_queue(book, ["第二章"], title="Chapter 2", chapter_number=2)
    assert db.claim_next_queue_item(book_id=book)
    with pytest.raises(ToolError, match="processing"):
        call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="Note.")


def test_finished_job_does_not_block(server, db, admin, book):
    add_entity(db, "陈元", "Chen Yuan", book)
    admin.jobs = {str(book): running_job(book, status="complete", is_running=False)}
    call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="Note.")


# ── entity note / gender ─────────────────────────────────────────────────────

def test_set_note_is_a_human_revision(server, db, book):
    eid = add_entity(db, "陈元", "Chen Yuan", book)
    call(server, "t9_set_entity_note", book_id=book, untranslated="陈元", note="Age 16.",
         reason="time skip")
    revs = db.list_note_revisions(entity_id=eid)
    assert revs[0]["author"] == "human" and revs[0]["new_note"] == "Age 16."


def _global_entity(db, category, untranslated, translation):
    with db._conn() as conn:
        conn.cursor().execute(
            "INSERT INTO entities (category, untranslated, translation, book_id) "
            "VALUES (?, ?, ?, NULL)", (category, untranslated, translation))


def test_set_note_ambiguous_key_needs_category(server, db, book):
    """(book_id, untranslated) is unique, so ambiguity only arises among
    global rows — which a book inherits."""
    _global_entity(db, "characters", "青云", "Qingyun")
    _global_entity(db, "places", "青云", "Azure Cloud")
    with pytest.raises(ToolError, match="ambiguous"):
        call(server, "t9_set_entity_note", book_id=book, untranslated="青云", note="x")
    out = call_json(server, "t9_set_entity_note", book_id=book, untranslated="青云",
                    note="x", category="places")
    assert out["category"] == "places"


def test_book_row_beats_global_row(server, db, book):
    _global_entity(db, "places", "青云", "Azure Cloud")
    eid = add_entity(db, "青云", "Qingyun", book)
    out = call_json(server, "t9_set_entity_note", book_id=book, untranslated="青云", note="x")
    assert out["entity_id"] == eid


def test_set_gender_refuses_ungendered_category(server, db, book):
    add_entity(db, "青云山", "Azure Cloud Mountain", book, category="places")
    with pytest.raises(ToolError, match="not gender-tracked"):
        call(server, "t9_set_entity_gender", book_id=book, untranslated="青云山", gender="male")


def test_set_gender(server, db, book):
    eid = add_entity(db, "陈元", "Chen Yuan", book, gender="male")
    out = call_json(server, "t9_set_entity_gender", book_id=book, untranslated="陈元",
                    gender="female", reason="ch3 pronouns")
    assert out["changed"] and db.get_entity_by_id(eid)["gender"] == "female"


# ── prose ────────────────────────────────────────────────────────────────────

def test_replace_dry_run_then_apply_then_undo(server, db, book):
    save_chapter(db, book, 1, ["第一章", "x"], ["Chapter 1: Azure Sect", "The Azure Sect rose."],
                 title="Azure Sect")
    save_chapter(db, book, 2, ["第二章", "y"], ["Chapter 2", "the azure sect fell."])
    prev = call_json(server, "t9_replace_in_chapters", book_id=book, query="Azure Sect",
                     replacement="Cerulean Sect")
    assert prev["dry_run"] and prev["affected_chapters"] == 2
    assert prev["title_replacements"] == 1
    assert db.get_chapter(book_id=book, chapter_number=1)["content"][1] == "The Azure Sect rose."

    res = call_json(server, "t9_replace_in_chapters", book_id=book, query="Azure Sect",
                    replacement="Cerulean Sect", chapters="1", dry_run=False)
    assert res["affected_chapters"] == 1
    assert db.get_chapter(book_id=book, chapter_number=1)["content"][1] == "The Cerulean Sect rose."
    assert db.get_chapter(book_id=book, chapter_number=2)["content"][1] == "the azure sect fell."

    call(server, "t9_undo_replace", book_id=book)
    assert db.get_chapter(book_id=book, chapter_number=1)["content"][1] == "The Azure Sect rose."
    with pytest.raises(ToolError, match="No replace to undo"):
        call(server, "t9_undo_replace", book_id=book)


def test_replace_bad_regex(server, book):
    with pytest.raises(ToolError, match="Invalid regex"):
        call(server, "t9_replace_in_chapters", book_id=book, query="(", replacement="",
             regex=True)


def test_search_chapters(server, db, book):
    save_chapter(db, book, 1, ["第一章", "陈元"], ["Chapter 1", "Chen Yuan waited."])
    assert "ch1" in call(server, "t9_search_chapters", book_id=book, query="chen yuan")


# ── jobs ─────────────────────────────────────────────────────────────────────

def test_status_reports_book_safety(server, db, admin, book):
    admin.jobs = {str(book): running_job(book)}
    out = call_json(server, "t9_translation_status", book_id=book)
    assert out["book"]["translating"] and not out["book"]["entity_writes_safe"]
    assert out["jobs"][str(book)]["run_options"]["translation_model"] == "claude:x"


def test_pause_already_idle(server, admin, book):
    out = call_json(server, "t9_pause_translation", book_id=book)
    assert out["already_idle"]
    assert ("stop_auto", book) not in admin.calls


def test_pause_waits_and_echoes_run_options(db, admin, book, monkeypatch):
    from mcp_server import guard
    admin.statuses = [
        {"jobs": {str(book): running_job(book)}},
        {"jobs": {str(book): running_job(book, auto_process=False)}},
        {"jobs": {}},
    ]
    monkeypatch.setattr(guard.time, "sleep", lambda s: None)
    out = call_json(build_server(db=db, admin=admin), "t9_pause_translation", book_id=book,
                    poll_seconds=2)
    assert ("stop_auto", book) in admin.calls
    assert out["stopped"] and out["waited_seconds"] == 4
    assert out["resume_hint"] == {"book_id": book, "translation_model": "claude:x",
                                  "no_review": True, "max_chapters": 5}


def test_pause_returns_early_when_parked_on_review(db, admin, book, monkeypatch):
    from mcp_server import guard
    admin.statuses = [
        {"jobs": {str(book): running_job(book)}},
        {"jobs": {str(book): running_job(book, status="awaiting_review")}},
    ]
    monkeypatch.setattr(guard.time, "sleep", lambda s: None)
    out = call_json(build_server(db=db, admin=admin), "t9_pause_translation", book_id=book)
    assert out["needs_human"] and not out["stopped"]


def test_pause_without_run_options_says_so(db, admin, book, monkeypatch):
    from mcp_server import guard
    admin.statuses = [{"jobs": {str(book): running_job(book, run_options=None)}}, {"jobs": {}}]
    monkeypatch.setattr(guard.time, "sleep", lambda s: None)
    out = call_json(build_server(db=db, admin=admin), "t9_pause_translation", book_id=book)
    assert out["resume_hint"] is None and "run_options" in out["resume_note"]


def test_resume_payload_and_errors(server, admin, book):
    from mcp_server.deps import AdminHTTPError
    out = call_json(server, "t9_resume_translation", book_id=book, max_chapters=5,
                    translation_model="claude:x", no_review=True)
    assert out["started"]
    payload = admin.calls[-1][1]
    assert payload["auto_process"] and payload["book_id"] == book
    assert payload["max_chapters"] == 5 and payload["no_review"]

    admin.process_next_error = AdminHTTPError(404, "No items in queue.")
    with pytest.raises(ToolError, match="queue is empty"):
        call(server, "t9_resume_translation", book_id=book)
    admin.process_next_error = AdminHTTPError(409, "Book is already translating.")
    with pytest.raises(ToolError, match="already translating"):
        call(server, "t9_resume_translation", book_id=book)


# ── notes / reindex / candidates ─────────────────────────────────────────────

def test_notes_as_of_rewinds(server, db, book):
    eid = add_entity(db, "陈元", "Chen Yuan", book, origin_chapter=1)
    db.set_entity_note(eid, "Age 12.", author="model", chapter_number=1)
    db.set_entity_note(eid, "Age 16.", author="model", chapter_number=40)
    assert call_json(server, "t9_notes_as_of", book_id=book, chapter=10,
                     entity="陈元")["note"] == "Age 12."
    assert "Age 16." in call(server, "t9_notes_as_of", book_id=book, chapter=50)


def test_reindex(server, db, book):
    add_entity(db, "陈元", "Chen Yuan", book)
    save_chapter(db, book, 1, ["第一章", "陈元来了。"], ["Chapter 1", "Chen Yuan came."])
    out = call_json(server, "t9_reindex_chapter_entities", book_id=book)
    assert out["chapters_indexed"] == 1 and out["rows_written"] >= 1


def _candidate(db, book, chapter, zh, en):
    db.record_footnote_scan(book, chapter, f"Chapter {chapter}", "test:model", "h",
                            [{"term_zh": zh, "term_en": en, "body": f"{en} note.",
                              "sentence": zh}])
    return [r for r in db.list_footnote_candidates(book, chapter=chapter)
            if r["term_zh"] == zh][0]["id"]


def test_candidates_list_and_decide(server, db, book):
    a = _candidate(db, book, 1, "太极", "Taiji")
    _candidate(db, book, 5, "太极", "Taiji")
    assert "#%d" % a in call(server, "t9_list_footnote_candidates", book_id=book)
    firsts = call_json(server, "t9_list_footnote_candidates", book_id=book,
                       first_mentions_only=True, format="json")
    assert firsts["total"] == 1
    assert call_json(server, "t9_decide_footnote_candidates", book_id=book, ids=[a],
                     decision="accepted")["updated"] == 1
    other = db.create_book(title="Other")
    with pytest.raises(ToolError, match="not in book"):
        call(server, "t9_decide_footnote_candidates", book_id=other, ids=[a],
             decision="rejected")


# ── read-side entity tools ───────────────────────────────────────────────────

@pytest.fixture
def glossary(db, book):
    add_entity(db, "陈元", "Chen Yuan", book, origin_chapter=1, gender="male")
    add_entity(db, "青云宗", "Azure Cloud Sect", book, category="organizations",
               origin_chapter=2)
    save_chapter(db, book, 1, ["第一章", "陈元来了。"], ["Chapter 1", "Chen Yuan came."])
    save_chapter(db, book, 2, ["第二章", "陈元去了青云宗。"],
                 ["Chapter 2", "Chen Yuan went to the Azure Cloud Sect."])
    return book


def test_list_entities_filters_and_pages(server, glossary):
    text = call(server, "t9_list_entities", book_id=glossary, origin_chapter="1")
    assert "陈元 -> Chen Yuan" in text and "青云宗" not in text
    page = call_json(server, "t9_list_entities", book_id=glossary, limit=1, format="json")
    assert page["total"] == 2 and page["has_more"]
    with pytest.raises(ToolError):
        call(server, "t9_list_entities", book_id=glossary, origin_chapter="bogus")


def test_search_entities(server, glossary):
    assert "青云宗" in call(server, "t9_search_entities", book_id=glossary, pattern="sect")
    assert "青云宗" not in call(server, "t9_search_entities", book_id=glossary,
                               pattern="sect", case_sensitive=True)
    with pytest.raises(ToolError):
        call(server, "t9_search_entities", book_id=glossary, pattern="(", regex=True)


def test_entity_context(server, glossary):
    text = call(server, "t9_entity_context", book_id=glossary, entities=["青云宗"])
    assert "chapter 2" in text and "陈元去了青云宗。" in text


def test_note_revisions_introduced(server, db, glossary):
    eid = db.get_entity_id(glossary, "陈元")
    db.set_entity_note(eid, "A farmer.", author="model", chapter_number=1)
    db.set_entity_note(eid, "A farmer. Joined the sect.", author="model", chapter_number=2)
    text = call(server, "t9_note_revisions", book_id=glossary, introduced="sect")
    assert "ch2" in text and "ch1 " not in text
    assert "No note revisions" in call(server, "t9_note_revisions", book_id=glossary,
                                       dropped="farmer")


def test_grep_book_labels_and_queue(server, db, glossary):
    db.add_to_queue(glossary, ["第三章", "陈元回家。"], title="Chapter 3", chapter_number=3)
    text = call(server, "t9_grep_book", book_id=glossary, pattern="陈元")
    assert "ch3 (queued)" in text and "3 match(es) in 3 chapter(s)" in text
    assert "ch3" not in call(server, "t9_grep_book", book_id=glossary, pattern="陈元",
                             include_queue=False).split("\n\n")[0]
    counts = call(server, "t9_grep_book", book_id=glossary, pattern="Chen", field="en",
                  mode="count")
    assert "ch1" in counts and "ch2" in counts


def test_backfill_dry_run_never_writes(server, db, admin, glossary):
    eid = db.get_entity_id(glossary, "青云宗")
    db.update_entity_by_id(eid, origin_chapter=9)
    admin.jobs = {str(glossary): running_job(glossary)}   # dry run is not guarded
    out = call_json(server, "t9_backfill_origin_chapter", book_id=glossary, mode="recompute")
    assert out["dry_run"] and out["changes"][0]["window"] == "[2, 9)"
    assert db.get_entity_by_id(eid)["origin_chapter"] == 9
    with pytest.raises(ToolError, match="translating"):
        call(server, "t9_backfill_origin_chapter", book_id=glossary, mode="recompute",
             apply=True)
    admin.jobs = {}
    out = call_json(server, "t9_backfill_origin_chapter", book_id=glossary, mode="recompute",
                    apply=True)
    assert out["applied"] == 1 and db.get_entity_by_id(eid)["origin_chapter"] == 2


# ── corrections ──────────────────────────────────────────────────────────────

def test_correct_entity_dry_run_then_apply(server, db, admin, glossary):
    prev = call_json(server, "t9_correct_entity", book_id=glossary, untranslated="青云宗",
                     translation="Cerulean Cloud Sect", mode="substitute")
    assert prev["dry_run"] and prev["chapter_substitutions"] == 1
    assert db.get_chapter(book_id=glossary, chapter_number=2)["content"][1].endswith(
        "Azure Cloud Sect.")
    admin.jobs = {str(glossary): running_job(glossary)}
    call(server, "t9_correct_entity", book_id=glossary, untranslated="青云宗",
         translation="X", mode="substitute")          # dry run: not guarded
    with pytest.raises(ToolError, match="translating"):
        call(server, "t9_correct_entity", book_id=glossary, untranslated="青云宗",
             translation="Cerulean Cloud Sect", mode="substitute", dry_run=False)
    admin.jobs = {}
    call(server, "t9_correct_entity", book_id=glossary, untranslated="青云宗",
         translation="Cerulean Cloud Sect", mode="substitute", dry_run=False)
    assert db.get_chapter(book_id=glossary, chapter_number=2)["content"][1].endswith(
        "Cerulean Cloud Sect.")


def test_correct_entity_safer_scopes_to_source_mentions(server, db, glossary):
    # ch1 mentions "Chen Yuan" in English without its source term → safer skips it.
    save_chapter(db, glossary, 3, ["第三章", "无关。"], ["Chapter 3", "Chen Yuan, unrelated."])
    call(server, "t9_correct_entity", book_id=glossary, untranslated="陈元",
         translation="Chen Yuanzhi", mode="safer", dry_run=False)
    assert db.get_chapter(book_id=glossary, chapter_number=3)["content"][1] == "Chen Yuan, unrelated."
    assert db.get_chapter(book_id=glossary, chapter_number=1)["content"][1] == "Chen Yuanzhi came."


def test_correct_entity_not_found(server, glossary):
    with pytest.raises(ToolError):
        call(server, "t9_correct_entity", book_id=glossary, untranslated="无名",
             translation="Nobody", dry_run=False)


def test_bulk_cascade_order(server, db, glossary):
    out = call_json(server, "t9_bulk_correct_entities", book_id=glossary, mode="substitute",
                    dry_run=False, corrections=[
                        {"untranslated": "青云宗", "translation": "Cerulean Sect"},
                        {"untranslated": "陈元", "translation": "Chen Yuanzhi"}])
    assert out["tally"] == {"updated": 2}
    line = db.get_chapter(book_id=glossary, chapter_number=2)["content"][1]
    assert line == "Chen Yuanzhi went to the Cerulean Sect."
    with pytest.raises(ToolError, match="Duplicate"):
        call(server, "t9_bulk_correct_entities", book_id=glossary, corrections=[
            {"untranslated": "陈元", "translation": "A"},
            {"untranslated": "陈元", "translation": "B"}])


def test_change_category(server, db, glossary):
    prev = call_json(server, "t9_change_entity_category", book_id=glossary,
                     untranslated=["青云宗"], new_category="places")
    assert prev["dry_run"]
    assert db.get_entity_by_id(db.get_entity_id(glossary, "青云宗"))["category"] == "organizations"
    call(server, "t9_change_entity_category", book_id=glossary, untranslated=["青云宗"],
         new_category="places", dry_run=False)
    assert db.get_entity_by_id(db.get_entity_id(glossary, "青云宗"))["category"] == "places"
    with pytest.raises(ToolError):
        call(server, "t9_change_entity_category", book_id=glossary, untranslated=["青云宗"],
             new_category="zzz-not-a-category")


def test_delete_entities(server, db, glossary):
    out = call_json(server, "t9_delete_entities", book_id=glossary,
                    untranslated=["青云宗", "无名"])
    assert out["dry_run"] and len(out["to_delete"]) == 1 and out["errors"]
    assert db.get_entity_id(glossary, "青云宗")
    out = call_json(server, "t9_delete_entities", book_id=glossary,
                    untranslated=["青云宗"], apply=True)
    assert out["deleted"] == 1 and db.get_entity_id(glossary, "青云宗") is None


# ── footnotes ────────────────────────────────────────────────────────────────

@pytest.fixture
def fn_book(db, book):
    save_chapter(db, book, 1, ["第一章 太极", "他练《太极拳》。"],
                 ["Chapter 1: Taiji", "He practised 《Taiji Fist》 daily."])
    save_chapter(db, book, 2, ["第二章", "再练太极拳。"], ["Chapter 2", "Taiji Fist again."])
    return book


def test_add_footnotes_dry_run_apply_rerun(server, db, fn_book):
    spec = [{"term": "Taiji Fist", "body": "A martial art."}]
    prev = call_json(server, "t9_add_footnotes", book_id=fn_book, footnotes=spec)
    assert prev["dry_run"] and prev["plan"][0]["chapter"] == 1
    assert "[1]" not in db.get_chapter(book_id=fn_book, chapter_number=1)["content"][1]
    out = call_json(server, "t9_add_footnotes", book_id=fn_book, footnotes=spec, dry_run=False)
    assert out["written"] == 1
    line = db.get_chapter(book_id=fn_book, chapter_number=1)["content"][1]
    assert "《Taiji Fist》[1]" in line          # marker hops the closing bracket
    again = call_json(server, "t9_add_footnotes", book_id=fn_book, footnotes=spec,
                      dry_run=False)
    assert again["placed"] == 0 and again["skipped_existing_body"] == ["Taiji Fist"]


def test_add_footnote_heading_warning(server, fn_book):
    out = call_json(server, "t9_add_footnotes", book_id=fn_book,
                    footnotes=[{"term": "Taiji", "body": "Supreme ultimate."}])
    assert out["warnings"] and "ch1" in out["warnings"][0]


def test_list_delete_last_and_reanchor(server, db, fn_book):
    call(server, "t9_add_footnotes", book_id=fn_book, dry_run=False,
         footnotes=[{"term": "Taiji Fist", "body": "A martial art."}])
    listing = call_json(server, "t9_list_footnotes", book_id=fn_book, format="json")
    fid = listing["footnotes"][0]["id"]
    assert listing["footnotes"][0]["number"] == 1
    assert "↳" in call(server, "t9_list_footnotes", book_id=fn_book)

    # A prose edit renames the anchor → orphan → reanchor.
    call(server, "t9_replace_in_chapters", book_id=fn_book, query="Taiji Fist",
         replacement="Taiji Boxing", chapters="1", dry_run=False)
    db.rerender_chapter_footnotes(listing["footnotes"][0]["chapter_id"])
    assert call_json(server, "t9_list_footnotes", book_id=fn_book, orphans_only=True,
                     format="json")["total"] == 1
    res = call_json(server, "t9_reanchor_footnote", book_id=fn_book, footnote_id=fid,
                    anchor="Taiji Boxing")
    assert res["status"] == "active"

    prev = call_json(server, "t9_delete_footnotes", book_id=fn_book, chapter=1, number=1)
    assert prev["dry_run"] and prev["matched"] == 1
    call(server, "t9_delete_footnotes", book_id=fn_book, chapter=1, number=1, apply=True)
    content = db.get_chapter(book_id=fn_book, chapter_number=1)["content"]
    assert not any("[1]" in l for l in content)
    with pytest.raises(ToolError):
        call(server, "t9_delete_footnotes", book_id=fn_book, chapter=1)   # no selector


def test_candidate_report_prune_export(server, db, fn_book):
    good = _candidate(db, fn_book, 1, "太极拳", "Taiji Fist")
    _candidate(db, fn_book, 2, "不存在", "Phantom")
    assert "太极拳" in call(server, "t9_footnote_candidate_report", book_id=fn_book)
    prune = call_json(server, "t9_prune_footnote_candidates", book_id=fn_book)
    assert [d["term_zh"] for d in prune["doomed"]] == ["不存在"]
    assert len(db.list_footnote_candidates(fn_book)) == 2
    call(server, "t9_prune_footnote_candidates", book_id=fn_book, apply=True)
    assert [r["id"] for r in db.list_footnote_candidates(fn_book)] == [good]
    exp = call_json(server, "t9_export_footnote_candidates", book_id=fn_book)
    assert exp["footnotes"] == [{"term": "Taiji Fist", "body": "Taiji Fist note."}]


def test_scan_dry_run_makes_no_model_call(server, db, fn_book, monkeypatch):
    import footnote_scan_core
    monkeypatch.setattr(footnote_scan_core, "scan_single_chapter",
                        lambda *a, **k: pytest.fail("model called"))
    out = call_json(server, "t9_scan_footnotes", book_id=fn_book, chapters="1-2")
    assert out["dry_run"] and out["to_scan"] == [1, 2]


# ── usage log ────────────────────────────────────────────────────────────────

def test_every_call_is_logged(server, book, usage_log):
    call(server, "t9_get_book", book_id=book)
    with pytest.raises(ToolError):
        call(server, "t9_get_book", book_id=9999)
    rows = [json.loads(l) for l in usage_log.read_text().splitlines()]
    assert [(r["event"], r["tool"], r["ok"], r["mode"]) for r in rows] == [
        ("call", "t9_get_book", True, "full"), ("call", "t9_get_book", False, "full")]
    assert rows[0]["book_id"] == book and rows[0]["result_chars"] > 0
    assert "No book" in rows[1]["error"]


def test_read_only_calls_are_marked(db, admin, book, usage_log):
    call(build_server(db=db, admin=admin, read_only=True), "t9_list_books")
    assert json.loads(usage_log.read_text())["mode"] == "readonly"


def test_listing_tools_logs_nothing(server, usage_log):
    tools_by_name(server)
    assert not usage_log.exists()


def test_usage_summary(tmp_path):
    from mcp_server.usage import summarise
    log = tmp_path / "u.log"

    def look(ch, tool):
        return {"ts": f"2026-09-29T10:{ch:02d}:30", "event": "call", "mode": "readonly",
                "caller": "translation", "book": 90, "chapter": ch, "tool": tool, "ok": True}

    rows = [{"ts": "2026-09-29T10:00:00", "event": "connect", "mode": "readonly",
             "caller": "translation", "book": 90, "chapter": 5},       # legacy line
            look(5, "t9_search_entities"), look(5, "t9_grep_book"), look(7, "t9_grep_book"),
            {"ts": "2026-09-29T10:59:00", "event": "call", "mode": "full",
             "tool": "t9_list_books", "ok": True}]
    log.write_text("\n".join(json.dumps(r) for r in rows))
    text = summarise(str(log))
    assert "4 tool call(s)" in text and "connect" not in text
    assert "3 lookup(s) over 2 chapter(s) (1.5 per chapter" in text
    assert "t9_grep_book" in text
