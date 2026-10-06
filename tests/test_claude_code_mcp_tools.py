"""claudecode provider + read-only MCP tools: who gets them, what the CLI is
told, and that lookup-turn prose never reaches the streamed JSON."""
import json
import types

import pytest

from providers.claude_code_provider import ClaudeCodeProvider, mcp_tools_section
from translation_engine import TranslationEngine
from modules.claude_code_tools_module import MODULE_ID


class _Cfg:
    claude_code_mcp_url = "http://127.0.0.1:8766/mcp"

    def __init__(self, global_on):
        self.claude_code_mcp_tools = global_on


class _Log:
    def warning(self, *a, **k):
        pass


def _kwargs(db, book, global_on, provider=None):
    stub = types.SimpleNamespace(config=_Cfg(global_on), entity_manager=db, logger=_Log())
    provider = provider or types.SimpleNamespace(supports_mcp_tools=True)
    return TranslationEngine._mcp_tools_kwargs(stub, provider, book, 12)


@pytest.fixture
def book(db):
    return db.get_book(db.create_book(title="Tools Book"))


def _set_module(db, book, value):
    db.update_book(book["id"], modules={MODULE_ID: value})
    return db.get_book(book["id"])


@pytest.mark.parametrize("override, global_on, expected", [
    (None, False, False),   # Auto follows the global default (off)
    (None, True, True),     # Auto follows the global default (on)
    (True, False, True),    # a book set On wins over the global off
    (False, True, False),   # a book set Off wins over the global on
])
def test_book_override_beats_global(db, book, override, global_on, expected):
    if override is not None:
        book = _set_module(db, book, override)
    got = _kwargs(db, book, global_on)
    assert bool(got) is expected
    if expected:
        tools = got["mcp_tools"]
        assert tools["book_id"] == book["id"] and tools["chapter_number"] == 12
        assert tools["max_turns"] == 8 and tools["url"].endswith("/mcp")


def test_other_providers_never_get_the_kwarg(db, book):
    book = _set_module(db, book, True)
    assert _kwargs(db, book, True, provider=types.SimpleNamespace()) == {}


def test_tools_section_names_book_and_chapter():
    text = mcp_tools_section({"book_id": 90, "book_title": "T", "chapter_number": 12,
                              "max_turns": 8})
    assert "book_id=90" in text and "chapter 12" in text and "At most 7 lookups" in text


def test_tools_section_offers_summaries_before_this_chapter():
    text = mcp_tools_section({"book_id": 90, "chapter_number": 12})
    assert 't9_get_chapter_summaries' in text and 'chapters="<12"' in text
    assert '"1-11"' in text
    # Chapter 1 has nothing earlier to bound; a non-numeric chapter must not raise.
    for ch in (1, None, "abc"):
        text = mcp_tools_section({"book_id": 90, "chapter_number": ch})
        assert "t9_get_chapter_summaries" in text and 'chapters="<' not in text


def test_mcp_config_headers():
    cfg = json.loads(ClaudeCodeProvider._mcp_config(
        {"url": "http://127.0.0.1:8766/mcp", "book_id": 90, "chapter_number": 12}))
    server = cfg["mcpServers"]["t9"]
    assert server["type"] == "http"
    assert server["headers"] == {"X-T9-Caller": "translation", "X-T9-Book": "90",
                                 "X-T9-Chapter": "12"}
    assert ClaudeCodeProvider._mcp_config(None) == '{"mcpServers":{}}'


# ── stream filtering ─────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, events):
        self.stdout = [json.dumps(e) + "\n" for e in events]
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _ev(inner):
    return {"type": "stream_event", "event": inner}


def _text(t):
    return _ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})


TOOL_RUN = [
    _ev({"type": "message_start"}),
    _text("Let me check the glossary first."),
    _ev({"type": "content_block_start", "content_block": {"type": "tool_use"}}),
    _ev({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
    {"type": "user", "message": {}},
    _ev({"type": "message_start"}),
    _text('{"content": '),
    _text('["x"]}'),
    _ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
    {"type": "result", "result": "", "is_error": False},
]


@pytest.fixture
def provider(tmp_path):
    return ClaudeCodeProvider(bin_path="/bin/true")


def _collect(provider, events, tools):
    return "".join(c.text or "" for c in provider._stream_iter(_Proc(events), None, tools=tools))


def test_lookup_turn_prose_is_dropped(provider):
    assert _collect(provider, TOOL_RUN, tools=True) == '{"content": ["x"]}'


def test_without_tools_everything_streams(provider):
    assert _collect(provider, TOOL_RUN, tools=False).startswith("Let me check")


def test_non_json_final_answer_is_still_delivered(provider):
    events = [_ev({"type": "message_start"}), _text("plain answer"),
              _ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
              {"type": "result", "result": "", "is_error": False}]
    assert _collect(provider, events, tools=True) == "plain answer"


def test_unreachable_server_drops_tools(provider, monkeypatch):
    import providers.claude_code_provider as mod
    monkeypatch.setattr(mod, "_mcp_reachable", lambda url: False)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    provider.chat_completion([{"role": "user", "content": "hi"}], model="m",
                             mcp_tools={"url": "http://127.0.0.1:1/mcp", "book_id": 1})
    assert "--allowedTools" not in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--mcp-config") + 1] == '{"mcpServers":{}}'


def test_reachable_server_adds_tools(provider, monkeypatch):
    import providers.claude_code_provider as mod
    monkeypatch.setattr(mod, "_mcp_reachable", lambda url: True)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    provider.chat_completion([{"role": "system", "content": "SYS"},
                              {"role": "user", "content": "hi"}], model="m",
                             mcp_tools={"url": "http://127.0.0.1:8766/mcp", "book_id": 7,
                                        "chapter_number": 3, "max_turns": 5})
    cmd = seen["cmd"]
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__t9"
    assert cmd[cmd.index("--max-turns") + 1] == "5"
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""
