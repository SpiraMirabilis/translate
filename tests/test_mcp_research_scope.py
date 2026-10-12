"""Synthetic research requests, including the real HTTP dispatch and concurrency."""
import asyncio
import json
import random
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel.server import request_ctx
from starlette.requests import Request

from mcp_server.research_scope import _witnessed_renderings, scope
from mcp_server.server import build_server
from research_tools import RESEARCH_TOOLS


def headers(book, chapter=2):
    return {"x-t9-caller": "translation", "x-t9-book": str(book), "x-t9-chapter": str(chapter)}


def context(values):
    request = Request({"type": "http", "headers": [(k.encode(), v.encode()) for k, v in values.items()]})
    return SimpleNamespace(request=request)


@pytest.fixture
def research(db, monkeypatch):
    monkeypatch.setenv("MCP_USAGE_LOG", "off")
    book = db.create_book(title="Synthetic Book")
    other = db.create_book(title="Unrelated Book")
    for number, word in ((1, "EARLIER"), (2, "CURRENT"), (3, "FUTURE")):
        db.save_chapter(book_id=book, chapter_number=number, title=word,
                        untranslated_content=[f"陈元 {word}"], translated_content=[f"Chen Yuan {word}"],
                        summary=word)
    db.save_chapter(book_id=other, chapter_number=1, title="OTHER_BOOK",
                    untranslated_content=["OTHER_BOOK"], translated_content=["OTHER_BOOK"])
    db.add_to_queue(book, ["QUEUED_FUTURE"], title="QUEUED_FUTURE", chapter_number=4)
    db.add_to_queue(book, ["QUEUED_CURRENT"], title="QUEUED_CURRENT", chapter_number=2)
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book, origin_chapter=1, gender="male")
    eid = db.get_entity_id(book, "陈元")
    db.set_entity_note(eid, "EARLIER_NOTE", author="model", chapter_number=1)
    db.set_entity_note(eid, "FUTURE_NOTE", author="model", chapter_number=3)
    server = build_server(db=db, read_only=True, stateless_http=True)
    return server, book, other


def call(server, values, name, **arguments):
    async def invoke():
        token = request_ctx.set(context(values))
        try:
            return (await server.call_tool(name, arguments))[0].text
        finally:
            request_ctx.reset(token)
    return asyncio.run(invoke())


@pytest.mark.parametrize("values", [{}, {"x-t9-caller": "translation"},
    {"x-t9-book": "1", "x-t9-chapter": "2"},
    {"x-t9-caller": "review", "x-t9-book": "1", "x-t9-chapter": "2"}])
def test_missing_or_ambiguous_scope_is_refused(research, values):
    server, book, _ = research
    with pytest.raises(ToolError):
        call(server, values, "t9_list_entities", book_id=book)


@pytest.mark.parametrize("key", ["x-t9-book", "x-t9-chapter"])
@pytest.mark.parametrize("value", ["", "-1", "0", "1.5", "NaN", "true", " 2", "abc", "²"])
def test_invalid_scope_is_refused(research, key, value):
    server, book, _ = research
    values = {**headers(book), key: value}
    with pytest.raises(ToolError):
        call(server, values, "t9_list_entities", book_id=book)


def test_unknown_and_cross_book_scope_are_refused(research):
    server, book, other = research
    for values, target in ((headers(book), other), (headers(9999), 9999)):
        with pytest.raises(ToolError, match="scope could not be verified"):
            call(server, values, "t9_list_entities", book_id=target)
    with pytest.raises(ToolError):
        call(server, headers(book), "t9_list_entities")


def test_unknown_chapter_is_refused_and_queued_chapter_is_valid(research):
    server, book, _ = research
    with pytest.raises(ToolError, match="chapter scope could not be verified"):
        call(server, headers(book, 9999), "t9_list_entities", book_id=book)
    assert "Chen Yuan" in call(server, headers(book, 4), "t9_list_entities", book_id=book)


@pytest.mark.parametrize("name,arguments", [
    ("t9_get_chapter", {"chapter_number": 3}),
    ("t9_notes_as_of", {"chapter": 3}),
    ("t9_list_entities", {"as_of_chapter": 3}),
    ("t9_list_entities", {"current_notes": True}),
    ("t9_list_entities", {"category": "characters"}),
    ("t9_list_entities", {"category": "cultivation terms"}),
    ("t9_search_entities", {"pattern": "Chen", "category": "cultivation terms"}),
    ("t9_list_books", {}), ("t9_get_book", {}), ("t9_note_revisions", {}),
    ("t9_set_entity_note", {"untranslated": "陈元", "note": "unsafe"}),
])
def test_future_or_unscoped_tools_are_refused(research, name, arguments):
    server, book, _ = research
    with pytest.raises(ToolError):
        call(server, headers(book), name, book_id=book, **arguments)
    assert scope.get() is None


@pytest.mark.parametrize("chapters", [None, "1-9999", ">0", "1,2,3,4", ">2"])
@pytest.mark.parametrize("name,arguments", [
    ("t9_get_chapter_summaries", {}),
    ("t9_grep_book", {"pattern": ".", "field": "both", "include_queue": True, "context": 5}),
    ("t9_entity_context", {"entities": ["陈元"], "mentions": "1,$"}),
])
def test_broad_queries_are_intersected_with_scope(research, name, arguments, chapters):
    server, book, _ = research
    text = call(server, headers(book), name, book_id=book, chapters=chapters, **arguments)
    assert "FUTURE" not in text and "OTHER_BOOK" not in text
    if chapters != ">2":
        assert "CURRENT" in text


def test_entity_reads_only_include_verified_historic_information(research, db):
    server, book, _ = research
    # These records must not appear, even if origin metadata claims they are early.
    for key, rendering, origin in (("未来", "FUTURE_ENTITY", 3), ("未知", "UNKNOWN", None),
                                    ("陈元", "FUTURE_RENDERING", 1)):
        if key != "陈元":
            db.add_entity("characters", key, rendering, book_id=book, origin_chapter=origin)
    with db._conn() as conn:
        conn.cursor().execute("INSERT INTO entities (category, untranslated, translation, book_id, origin_chapter) "
                              "VALUES ('characters', '全球', 'GLOBAL_SECRET', NULL, 1)")
    for name, args in (("t9_list_entities", {}), ("t9_search_entities", {"pattern": "*"}),
                       ("t9_notes_as_of", {"chapter": 2, "entity": "陈元"})):
        text = call(server, headers(book), name, book_id=book, **args)
        assert "EARLIER_NOTE" in text and "Chen Yuan" in text
        for hidden in ("FUTURE_NOTE", "UNKNOWN", "GLOBAL_SECRET", "FUTURE_ENTITY",
                       "current_note", "last_chapter", "gender", "category"):
            assert hidden not in text
    eid = db.get_entity_id(book, "陈元")
    with db._conn() as conn:
        conn.cursor().execute("UPDATE entities SET translation = 'FUTURE_RENDERING' WHERE id = ?", (eid,))
    assert "FUTURE_RENDERING" not in call(server, headers(book), "t9_list_entities", book_id=book)


@pytest.mark.parametrize("damage", ["undated", "missing_creation", "out_of_order", "broken_chain"])
def test_unverifiable_note_history_is_omitted(research, db, damage):
    server, book, _ = research
    eid = db.get_entity_id(book, "陈元")
    with db._conn() as conn:
        cur = conn.cursor()
        if damage == "undated":
            cur.execute("UPDATE entity_note_revisions SET chapter_number = NULL WHERE entity_id = ?", (eid,))
        elif damage == "missing_creation":
            cur.execute("DELETE FROM entity_note_revisions WHERE entity_id = ? AND previous_note IS NULL", (eid,))
        elif damage == "out_of_order":
            cur.execute("UPDATE entity_note_revisions SET chapter_number = 1 WHERE entity_id = ? AND chapter_number = 3", (eid,))
            cur.execute("UPDATE entity_note_revisions SET chapter_number = 2 WHERE entity_id = ? AND previous_note IS NULL", (eid,))
        else:
            cur.execute("UPDATE entity_note_revisions SET previous_note = 'UNVERIFIABLE' WHERE entity_id = ?", (eid,))
    text = call(server, headers(book), "t9_list_entities", book_id=book)
    assert "Chen Yuan" in text and "note" not in text


def test_review_clients_keep_unrestricted_behavior(research, db):
    server, book, _ = research
    assert "FUTURE" in asyncio.run(server.call_tool("t9_get_chapter", {"book_id": book, "chapter_number": 3}))[0].text
    review = build_server(db=db)
    assert "FUTURE" in call(review, {}, "t9_get_chapter", book_id=book, chapter_number=3)
    with pytest.raises(ToolError):
        call(review, headers(book), "t9_get_chapter", book_id=book, chapter_number=1)


def test_concurrent_scopes_do_not_leak(research, monkeypatch):
    server, book, other = research
    from mcp.server.fastmcp import FastMCP
    original = FastMCP.call_tool
    arrived = 0

    async def run():
        barrier = asyncio.Event()

        async def interleaved(self, name, arguments):
            nonlocal arrived
            before = scope.get()
            arrived += 1
            if arrived == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 2)
            assert scope.get() == before
            return await original(self, name, arguments)

        monkeypatch.setattr(FastMCP, "call_tool", interleaved)

        async def invoke(target, chapter):
            token = request_ctx.set(context(headers(target, chapter)))
            try:
                result = await server.call_tool("t9_get_chapter_summaries", {"book_id": target})
                assert scope.get() is None
                return result[0].text
            finally:
                request_ctx.reset(token)
        return await asyncio.gather(invoke(book, 1), invoke(other, 1))

    first, second = asyncio.run(run())
    assert "EARLIER" in first and "CURRENT" not in first and "OTHER_BOOK" not in first
    assert "OTHER_BOOK" in second and "EARLIER" not in second
    assert scope.get() is None


def test_http_dispatch_exposes_only_scoped_tools(research):
    from providers.claude_code_provider import ClaudeCodeProvider
    server, book, _ = research
    config = json.loads(ClaudeCodeProvider._mcp_config({
        "url": "http://testserver/mcp", "book_id": book, "chapter_number": 2}))
    claude = config["mcpServers"]["t9"]

    async def run():
        app = server.streamable_http_app()
        async with server.session_manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                health = (await client.get("/health")).json()
                assert health == {"ok": True, "read_only": True, "research_scope_version": 1}
                common = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-03-26"}
                scoped = {**common, **claude["headers"]}
                initialization = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "mock-claude-cli", "version": "test"}}}
                initialized = (await client.post(claude["url"], headers=scoped, json=initialization)).json()
                assert initialized["result"]["serverInfo"]["name"] == "t9"
                notification = await client.post(claude["url"], headers=scoped,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                assert notification.status_code == 202
                payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                response = await client.post(claude["url"], headers=scoped, json=payload)
                tools = {t["name"]: t for t in response.json()["result"]["tools"]}
                assert set(tools) == set(RESEARCH_TOOLS)
                for name in ("t9_list_entities", "t9_search_entities"):
                    description = tools[name]["inputSchema"]["properties"]["category"]["description"]
                    assert "Omit during scoped translation research" in description
                    assert "historical category filtering is unavailable" in description
                # Use Claude's exact config to invoke every advertised research tool.
                for name, arguments in [
                    ("t9_search_entities", {"pattern": "陈元"}),
                    ("t9_list_entities", {}),
                    ("t9_grep_book", {"pattern": "Chen", "field": "both", "chapters": "1-9999"}),
                    ("t9_get_chapter_summaries", {"chapters": "1-9999"}),
                    ("t9_get_chapter", {"chapter_number": 1}),
                    ("t9_entity_context", {"entities": ["陈元"], "chapters": "1-9999"}),
                    ("t9_notes_as_of", {"chapter": 2, "entity": "陈元"}),
                ]:
                    lookup = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": name, "arguments": {"book_id": book, **arguments}}}
                    result = (await client.post(claude["url"], headers=scoped,
                                                json=lookup)).json()["result"]
                    assert not result.get("isError"), result
                    text = "".join(c.get("text", "") for c in result["content"])
                    assert text and "FUTURE" not in text and "OTHER_BOOK" not in text
                    assert "Chen Yuan" in text or "EARLIER" in text
                unknown = (await client.post("/mcp", headers={**common, **headers(book, 9999)}, json=payload)).json()
                assert "error" in unknown
                payload.update(method="tools/call", params={"name": "t9_get_chapter", "arguments": {"book_id": book, "chapter_number": 3}})
                blocked = (await client.post("/mcp", headers={**common, **headers(book)}, json=payload)).json()
                assert blocked["result"]["isError"] and "FUTURE" not in json.dumps(blocked)
                payload["params"]["arguments"]["chapter_number"] = 1
                allowed = (await client.post("/mcp", headers={**common, **headers(book)}, json=payload)).json()
                assert not allowed["result"].get("isError") and "EARLIER" in json.dumps(allowed)
                missing = (await client.post("/mcp", headers=common, json=payload)).json()
                assert missing["result"]["isError"]
    asyncio.run(run())


def test_witness_matching_preserves_overlaps_prefixes_and_literal_patterns():
    keys = ["张", "张三", "张三丰", "三丰山", "[x]", "a.b", "x|y", "look?", "*", "missing"]
    entities = [{"untranslated": key, "translation": f"rendering-{i:03d}"} for i, key in enumerate(keys)]
    rows = [{"untranslated_content": json.dumps(["张三丰山 [x] a.b x|y look? * missing"]),
             "translated_content": json.dumps([" ".join(ent["translation"] for ent in entities[:-1])])},
            {"untranslated_content": "unrelated source", "translated_content": "rendering-009"}]
    assert _witnessed_renderings(entities, rows) == {
        (ent["untranslated"], ent["translation"]) for ent in entities[:-1]}


def test_witness_matching_agrees_with_original_predicate():
    rng = random.Random(33)
    alphabet = "张三丰山abc.*|?"
    entities = [{"untranslated": "".join(rng.choices(alphabet, k=rng.randrange(1, 5))),
                 "translation": "".join(rng.choices("abcdef", k=rng.randrange(2, 6)))}
                for _ in range(200)]
    rows = [{"untranslated_content": "".join(rng.choices(alphabet, k=100)),
             "translated_content": "".join(rng.choices("abcdef", k=100))} for _ in range(15)]
    expected = {(ent["untranslated"], ent["translation"]) for ent in entities
                if any(ent["untranslated"] in row["untranslated_content"]
                       and ent["translation"] in row["translated_content"] for row in rows)}
    assert _witnessed_renderings(entities, rows) == expected
