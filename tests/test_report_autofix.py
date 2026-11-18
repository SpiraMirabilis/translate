"""Reader error report Auto-fix (web/services/report_autofix.py).

No live model: a fake provider returns canned JSON. What's exercised is the
validation (the model never writes anything unchecked), applying edits /
footnotes / gender corrections, the optimistic lock, and the admin endpoints.
"""
import json

import pytest

from web.services import report_autofix as ra

SOURCE = ["邓丙丁看着学生们。", "他拿起了火器。", "本站网址：xx.com"]
PROSE = [
    "Deng Bingding looked at the students and smiled to himself.",
    "He picked up the firearms and checked his ammunition.",
    "Read at xx.com for the latest chapters.",
]


class FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat_completion(self, messages, model, **kw):
        self.calls.append(messages)
        return {"content": self.reply}

    def get_response_content(self, response):
        return response["content"]


class FakeConfig:
    def __init__(self, reply):
        self.provider = FakeProvider(reply if isinstance(reply, str) else json.dumps(reply))

    def get_client(self, spec):
        return self.provider, "fake-model"


def _book(db, prose=PROSE, source=SOURCE, n=3):
    book_id = db.create_book("Autofix Book", author="A")
    db.save_chapter(book_id, n, f"Chapter {n}", list(source), list(prose))
    return book_id


def _report(db, book_id, n=3, **kw):
    data = {"book_id": book_id, "chapter_number": n, "report_type": "other",
            "quote": PROSE[0], "problem": "Deng Bingding is female"}
    data.update(kw)
    return db.get_error_report(db.create_error_report(data))


def _ctx(db, book_id, n=3):
    return ra.load_context(db, {"book_id": book_id, "chapter_number": n})


# ------------------------------------------------------------------
# Edit validation / application
# ------------------------------------------------------------------

def test_validate_rejects_bad_edits():
    edits = [
        {"line": 0, "find": "himself", "replace": "herself", "reason": "pronoun"},
        {"line": 9, "find": "x", "replace": "y"},                        # out of range
        {"line": 1, "find": "rifle", "replace": "musket"},               # not found
        {"line": 0, "find": "the", "replace": "a"},                      # ok? "the" once
        {"line": 0, "find": "students and smiled", "replace": "pupils and smiled"},  # overlaps? no
        {"line": 0, "find": "smiled to himself", "replace": "grinned"},  # overlaps edit 0
        {"line": 1, "find": "e", "replace": "E"},                        # not unique
        {"line": 1, "find": "He", "replace": "He"},                      # no change
    ]
    ok, bad = ra.validate_edits(PROSE, edits)
    assert [e["id"] for e in ok] == [0, 3, 4]
    whys = [b["why"] for b in bad]
    assert any("doesn't exist" in w for w in whys)
    assert any("not found" in w for w in whys)
    assert any("overlaps" in w for w in whys)
    assert any("not unique" in w for w in whys)
    assert any("no change" in w for w in whys)


def test_sentinels_are_untouchable():
    prose = ["A picture ⟦IMG:12⟧ of ⟦U⟧him⟦/U⟧ here."]
    ok, bad = ra.validate_edits(prose, [
        {"line": 0, "find": "⟦IMG:12⟧ of", "replace": "of"},
        {"line": 0, "find": "⟦U⟧him⟦/U⟧", "replace": "⟦U⟧her⟦/U⟧"},
    ])
    assert [e["id"] for e in ok] == [1]
    assert "⟦" in bad[0]["why"]


def test_apply_replace_split_delete():
    edits, bad = ra.validate_edits(PROSE, [
        {"line": 0, "find": "himself", "replace": "herself"},
        {"line": 0, "find": "Deng Bingding", "replace": "Deng Bingding"[::1] + ""},  # no-op → rejected
        {"line": 1, "find": "He picked up the firearms and ",
         "replace": "He picked up the firearms.\nThen he "},
        {"line": 2, "find": PROSE[2], "replace": ""},
    ])
    assert len(bad) == 1
    new, line_map = ra.apply_edits(PROSE, edits)
    assert new == [
        "Deng Bingding looked at the students and smiled to herself.",
        "He picked up the firearms.",
        "Then he checked his ammunition.",
    ]
    assert line_map == [[0], [1, 2], []]
    kinds = {e["line"]: e["kind"] for e in edits}
    assert kinds == {0: "replace", 1: "split", 2: "delete"}


def test_word_diff():
    segs = ra.word_diff("smiled to himself.", "smiled to herself.")
    assert ["del", "himself"] in segs and ["ins", "herself"] in segs
    assert "".join(t for op, t in segs if op != "ins") == "smiled to himself."


def test_edit_orphaning_a_footnote_is_rejected(db):
    book_id = _book(db)
    ch = db.get_chapter(book_id=book_id, chapter_number=3)
    db.add_footnote(book_id, ch["id"], "firearms", "Firearms (火器): a note.")
    db.rerender_chapter_footnotes(ch["id"])
    ctx = _ctx(db, book_id)
    assert not ctx["untracked_footnotes"]
    assert "[1]" not in "".join(ctx["prose"])        # the model sees clean prose
    ok, bad = ra.validate_edits(ctx["prose"], [
        {"line": 1, "find": "firearms", "replace": "guns"},
        {"line": 1, "find": "his ammunition", "replace": "the ammunition"},
    ], footnote_rows=ctx["chapter_footnote_rows"])
    assert [e["id"] for e in ok] == [1]
    assert "orphan" in bad[0]["why"]


# ------------------------------------------------------------------
# Untracked (pre-migration) footnotes
# ------------------------------------------------------------------

def test_untracked_footnotes_are_kept_and_block_new_ones(db):
    rendered = [PROSE[0], "He picked up the firearms[1] and checked his ammunition.",
                PROSE[2], "", "[1] Firearms: an inline note nobody migrated."]
    book_id = _book(db, prose=rendered)
    ctx = _ctx(db, book_id)
    assert ctx["untracked_footnotes"]
    assert ctx["def_block"][-1].startswith("[1] Firearms")
    ok, bad = ra.validate_edits(ctx["prose"], [
        {"line": 1, "find": "firearms[1]", "replace": "guns"},          # drops the marker
        {"line": 1, "find": "his ammunition", "replace": "the ammunition"},
        {"line": 1, "find": rendered[1], "replace": ""},                 # deletes a marked line
    ], untracked=True)
    assert [e["id"] for e in ok] == [1]
    fn_ok, fn_bad = ra.validate_footnotes(ctx["prose"], [[0], [1], [2]],
                                          [{"line": 0, "anchor": "students", "body": "x"}], ctx)
    assert fn_ok == [] and "backfill_footnotes" in fn_bad[0]["why"]

    report = _report(db, book_id)
    cfg = FakeConfig({"verdict": "fix", "explanation": "e",
                      "edits": [{"line": 1, "find": "his ammunition", "replace": "the ammunition"}]})
    db.set_error_report_autofix(report["id"], "done", ra.propose(db, cfg, report))
    ra.apply_proposal(db, db.get_error_report(report["id"]))
    saved = db.get_chapter(book_id=book_id, chapter_number=3)["content"]
    assert saved[1] == "He picked up the firearms[1] and checked the ammunition."
    assert saved[-1] == "[1] Firearms: an inline note nobody migrated."


# ------------------------------------------------------------------
# Footnotes
# ------------------------------------------------------------------

def test_footnote_validation(db):
    book_id = _book(db)
    db.save_chapter(book_id, 1, "Chapter 1", ["x"], ["They also had gunpowder weapons back then."])
    ctx = _ctx(db, book_id)
    edits, _ = ra.validate_edits(ctx["prose"], [
        {"line": 1, "find": "firearms", "replace": "gunpowder weapons"}])
    new, line_map = ra.apply_edits(ctx["prose"], edits)
    ok, bad = ra.validate_footnotes(new, line_map, [
        {"line": 1, "anchor": "gunpowder weapons", "source_term": "火器",
         "body": "Gunpowder weapons (火器 huǒqì): early firearms, rockets and bombs."},
        {"line": 0, "anchor": "nonexistent words", "body": "x"},
        {"line": 1, "anchor": "ammunition", "body": ""},
    ], ctx, db=db)
    assert len(ok) == 1 and len(bad) == 2
    fn = ok[0]
    assert fn["number"] == 1 and fn["occurrence"] == 1
    assert "gunpowder weapons[1]" in fn["preview"]
    assert any("first mention is ch1" in w for w in fn["warnings"])


def test_duplicate_body_rejected(db):
    book_id = _book(db)
    ch = db.get_chapter(book_id=book_id, chapter_number=3)
    db.add_footnote(book_id, ch["id"], "students", "Same body.")
    ctx = _ctx(db, book_id)
    ok, bad = ra.validate_footnotes(ctx["prose"], [[0], [1], [2]], [
        {"line": 1, "anchor": "ammunition", "body": "Same body."},
        {"line": 0, "anchor": "students", "body": "Another."},       # spot already taken
    ], ctx)
    assert ok == []
    assert "already in the book" in bad[0]["why"]
    assert "already has a footnote" in bad[1]["why"]


# ------------------------------------------------------------------
# Propose → apply (the 火器 case end to end)
# ------------------------------------------------------------------

REPLY = {
    "verdict": "fix",
    "explanation": "火器 covers early gunpowder weapons; reworded and footnoted.",
    "edits": [
        {"line": 1, "find": "firearms", "replace": "gunpowder weapons", "reason": "period"},
        {"line": 2, "find": PROSE[2], "replace": "", "reason": "source ad"},
    ],
    "footnotes": [{"line": 1, "anchor": "gunpowder weapons", "source_term": "火器",
                   "body": "Gunpowder weapons (火器 huǒqì): every early gunpowder weapon."}],
    "entity_updates": [],
    "other_action": "",
}


def test_propose_and_apply(db):
    book_id = _book(db)
    report = _report(db, book_id, report_type="mistranslation",
                     quote="He picked up the firearms", problem="no firearms in this era?")
    cfg = FakeConfig("Thinking...\n" + json.dumps(REPLY))
    proposal = ra.propose(db, cfg, report)
    assert proposal["verdict"] == "fix"
    assert len(proposal["edits"]) == 2 and len(proposal["footnotes"]) == 1
    user_msg = cfg.provider.calls[0][1]["content"]
    assert "T1: He picked up the firearms" in user_msg and "S1: 他拿起了火器。" in user_msg

    db.set_error_report_autofix(report["id"], "done", proposal)
    applied = ra.apply_proposal(db, db.get_error_report(report["id"]))
    assert applied["edits"] == [0, 1] and len(applied["footnotes"]) == 1

    content = db.get_chapter(book_id=book_id, chapter_number=3)["content"]
    assert content[1] == "He picked up the gunpowder weapons[1] and checked his ammunition."
    assert "Read at" not in " ".join(content)
    assert content[-1].startswith("[1] Gunpowder weapons (火器")

    row = db.get_error_report(report["id"])
    assert row["status"] == "resolved"
    assert row["admin_notes"].startswith("火器 covers")
    assert json.loads(row["autofix_result"])["applied"]
    revs = db.list_chapter_revisions(book_id, 3)
    assert revs and db.get_chapter_revision(revs[0]["id"])["content"][2] == PROSE[2]

    with pytest.raises(ra.AutofixError, match="already applied"):
        ra.apply_proposal(db, db.get_error_report(report["id"]))


def test_apply_subset_with_edited_footnote(db):
    book_id = _book(db)
    report = _report(db, book_id)
    db.set_error_report_autofix(report["id"], "done", ra.propose(db, FakeConfig(REPLY), report))
    ra.apply_proposal(db, db.get_error_report(report["id"]), edit_ids=[0],
                      footnotes=[{"id": 0, "body": "Edited body."}], notes="by hand")
    content = db.get_chapter(book_id=book_id, chapter_number=3)["content"]
    assert content[2] == PROSE[2]                       # the deletion wasn't chosen
    assert content[-1] == "[1] Edited body."
    assert db.get_error_report(report["id"])["admin_notes"] == "by hand"


def test_apply_refuses_a_changed_chapter(db):
    book_id = _book(db)
    report = _report(db, book_id)
    db.set_error_report_autofix(report["id"], "done", ra.propose(db, FakeConfig(REPLY), report))
    import time
    time.sleep(0.01)
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE, PROSE[:2])
    with pytest.raises(ra.ChapterChanged):
        ra.apply_proposal(db, db.get_error_report(report["id"]))


def test_gender_update(db):
    book_id = _book(db)
    db.add_entity("characters", "邓丙丁", "Deng Bingding", book_id=book_id, gender="male")
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE, PROSE)   # reindex chapter_entities
    ctx = _ctx(db, book_id)
    term = next(t for t in ctx["terms"] if t["translation"] == "Deng Bingding")
    ok, bad = ra.validate_entity_updates([
        {"entity_id": term["id"], "field": "gender", "value": "female", "reason": "她"},
        {"entity_id": term["id"], "field": "translation", "value": "Deng B"},
        {"entity_id": 999999, "value": "female"},
    ], ctx)
    assert len(ok) == 1 and len(bad) == 2

    report = _report(db, book_id)
    reply = {"verdict": "fix", "explanation": "She is female.",
             "edits": [{"line": 0, "find": "himself", "replace": "herself"}],
             "entity_updates": [{"entity_id": term["id"], "field": "gender", "value": "female"}]}
    db.set_error_report_autofix(report["id"], "done", ra.propose(db, FakeConfig(reply), report))
    # Opt-in: not applied unless chosen.
    ra.apply_proposal(db, db.get_error_report(report["id"]))
    assert db.get_entity_by_id(term["id"])["gender"] == "male"


def test_gender_update_applied_when_chosen(db):
    book_id = _book(db)
    db.add_entity("characters", "邓丙丁", "Deng Bingding", book_id=book_id, gender="male")
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE, PROSE)
    term = next(t for t in _ctx(db, book_id)["terms"] if t["translation"] == "Deng Bingding")
    report = _report(db, book_id)
    reply = {"verdict": "fix", "explanation": "x",
             "entity_updates": [{"entity_id": term["id"], "field": "gender", "value": "female"}]}
    db.set_error_report_autofix(report["id"], "done", ra.propose(db, FakeConfig(reply), report))
    applied = ra.apply_proposal(db, db.get_error_report(report["id"]), entity_update_ids=[0])
    assert applied["entity_updates"][0]["value"] == "female"
    assert db.get_entity_by_id(term["id"])["gender"] == "female"


def test_bad_json_and_unknown_verdict(db):
    book_id = _book(db)
    report = _report(db, book_id)
    with pytest.raises(ra.AutofixError, match="invalid JSON"):
        ra.propose(db, FakeConfig("not json at all {"), report)
    p = ra.propose(db, FakeConfig({"verdict": "maybe", "edits": "nope"}), report)
    assert p["verdict"] == "unclear" and p["edits"] == []


def test_book_wide_report_refused(db):
    book_id = _book(db)
    with pytest.raises(ra.AutofixError, match="Book-wide"):
        ra.load_context(db, {"book_id": book_id, "chapter_number": None})


# ------------------------------------------------------------------
# Admin endpoints
# ------------------------------------------------------------------

class _SyncThread:
    def __init__(self, target, args=(), kwargs=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def test_endpoints(db, admin_client, monkeypatch):
    from web.api import error_reports_admin

    monkeypatch.setattr(error_reports_admin.threading, "Thread", _SyncThread)
    monkeypatch.setattr(error_reports_admin, "_config", FakeConfig(REPLY))

    book_id = _book(db)
    report = _report(db, book_id)
    wide = db.create_error_report({"book_id": book_id, "report_type": "other", "problem": "x"})

    assert admin_client.post(f"/api/error-reports/{wide}/autofix").status_code == 400

    r = admin_client.post(f"/api/error-reports/{report['id']}/autofix")
    assert r.status_code == 200, r.text
    got = admin_client.get(f"/api/error-reports/{report['id']}").json()
    assert got["autofix_status"] == "done"
    assert got["autofix_result"]["verdict"] == "fix"     # decoded, not a string

    r = admin_client.post(f"/api/error-reports/{report['id']}/autofix/apply", json={})
    assert r.status_code == 200, r.text
    assert admin_client.get(f"/api/error-reports/{report['id']}").json()["status"] == "resolved"

    # Re-run, then the chapter changes under the proposal → 409.
    admin_client.post(f"/api/error-reports/{report['id']}/autofix")
    import time
    time.sleep(0.01)
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE, ["Something else entirely."])
    r = admin_client.post(f"/api/error-reports/{report['id']}/autofix/apply", json={})
    assert r.status_code == 409


def test_running_reattaches(db, admin_client, monkeypatch):
    from web.api import error_reports_admin

    started = []
    monkeypatch.setattr(error_reports_admin.threading, "Thread",
                        lambda **kw: started.append(kw) or _SyncThread(lambda *a, **k: None))
    monkeypatch.setattr(error_reports_admin, "_config", FakeConfig(REPLY))
    book_id = _book(db)
    report = _report(db, book_id)
    admin_client.post(f"/api/error-reports/{report['id']}/autofix")
    admin_client.post(f"/api/error-reports/{report['id']}/autofix")
    assert len(started) == 1
    assert db.fail_stale_error_report_autofix() == 1
    assert db.get_error_report(report["id"])["autofix_status"] == "failed"


# ------------------------------------------------------------------
# Research tools (claudecode / codex)
# ------------------------------------------------------------------

def test_research_tools_attached_only_for_mcp_providers(db, monkeypatch):
    import settings_store
    from research_tools import mcp_tools_section

    book_id = _book(db)
    report = _report(db, book_id)
    reply = {"verdict": "no_change", "explanation": "fine"}

    seen = {}

    class McpProvider(FakeProvider):
        supports_mcp_tools = True

        def chat_completion(self, messages, model, **kw):
            seen.update(kw)
            return super().chat_completion(messages, model)

    cfg = FakeConfig(reply)
    cfg.provider = McpProvider(json.dumps(reply))
    p = ra.propose(db, cfg, report)
    tools = seen["mcp_tools"]
    assert p["research"] is True
    assert (tools["book_id"], tools["chapter_number"]) == (book_id, 3)
    assert "fixing a reader-reported error in book_id=" in mcp_tools_section(tools)
    assert "RESEARCH:" in cfg.provider.calls[0][-1]["content"]

    # Plain providers never see the kwarg (OpenAI-compatible ones would forward it).
    plain = FakeConfig(reply)
    assert ra.propose(db, plain, report)["research"] is False

    # The switch turns it off.
    seen.clear()
    monkeypatch.setattr(settings_store, "get",
                        lambda k, d=None: False if k == "error_report_fix_research" else d)
    cfg.provider = McpProvider(json.dumps(reply))
    ra.propose(db, cfg, report)
    assert "mcp_tools" not in seen


def test_translation_research_wording_unchanged():
    from research_tools import mcp_tools_section
    text = mcp_tools_section({"book_id": 5, "book_title": "B", "chapter_number": 9, "max_turns": 8})
    assert 'You are translating book_id=5 ("B"), chapter 9.' in text
