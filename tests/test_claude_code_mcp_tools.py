"""claudecode provider + read-only MCP tools: who gets them, what the CLI is
told, and that lookup-turn prose never reaches the streamed JSON."""
import io
import json
from pathlib import Path
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


def _kwargs(db, book, global_on, provider=None, chapter=12):
    stub = types.SimpleNamespace(config=_Cfg(global_on), entity_manager=db, logger=_Log())
    provider = provider or types.SimpleNamespace(supports_mcp_tools=True)
    return TranslationEngine._mcp_tools_kwargs(stub, provider, book, chapter)


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
@pytest.mark.parametrize("provider_name", ["claudecode", "codex"])
def test_book_override_beats_global(db, book, override, global_on, expected, provider_name):
    from providers.codex_provider import CodexProvider
    cls = ClaudeCodeProvider if provider_name == "claudecode" else CodexProvider
    provider = cls(bin_path="/bin/true")
    if override is not None:
        book = _set_module(db, book, override)
    got = _kwargs(db, book, global_on, provider=provider)
    assert bool(got) is expected
    if expected:
        tools = got["mcp_tools"]
        assert tools["book_id"] == book["id"] and tools["chapter_number"] == 12
        assert tools["max_turns"] == 8 and tools["url"].endswith("/mcp")


def test_other_providers_never_get_the_kwarg(db, book):
    book = _set_module(db, book, True)
    assert _kwargs(db, book, True, provider=types.SimpleNamespace()) == {}


@pytest.mark.parametrize("chapter", [None, "abc", 0, -1, True, 1.5])
def test_unknown_scope_disables_research(db, book, chapter):
    book = _set_module(db, book, True)
    assert _kwargs(db, book, True, chapter=chapter) == {}


def test_legacy_module_identifier_and_settings_are_preserved(db, book):
    from modules import REGISTRY
    assert MODULE_ID == "claude_code_tools"
    assert REGISTRY[MODULE_ID].name == "Research tools"
    book = _set_module(db, book, True)
    db.set_module_settings(book["id"], MODULE_ID, {"max_turns": 4})
    assert _kwargs(db, book, False)["mcp_tools"]["max_turns"] == 4


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
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "private lookup result"}]}},
    _ev({"type": "message_start"}),
    _ev({"type": "content_block_delta", "delta": {
        "type": "thinking_delta", "thinking": "private reasoning"}}),
    _text('{"content": '),
    _text('["x"]}'),
    _ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": '{"content": ["x"]}'}]}},
    {"type": "result", "result": "", "is_error": False},
]


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_KEEP_SESSIONS", "1")
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


def test_assistant_only_answer_after_failed_lookup(provider):
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me check."}, {"type": "tool_use"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": "MCP unavailable"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": '{"content": ["x"]}'}]}},
        {"type": "result", "is_error": False},
    ]
    assert _collect(provider, events, tools=True) == '{"content": ["x"]}'


@pytest.fixture
def cli_call(provider, monkeypatch):
    """Exercise both subprocess paths without a CLI, login, or network."""
    import providers.claude_code_provider as mod
    monkeypatch.delenv("CLAUDE_CODE_DEBUG", raising=False)
    seen = {}

    def capture(cmd, kw):
        seen.update(cmd=cmd, kwargs=kw)
        seen["system_path"] = Path(cmd[cmd.index("--system-prompt-file") + 1])
        seen["system"] = seen["system_path"].read_text(encoding="utf-8")

    def fake_run(cmd, **kw):
        capture(cmd, kw)
        seen["input"] = kw["input"]
        return types.SimpleNamespace(returncode=0, stdout='```json\n{"content": ["x"]}\n```', stderr="")

    class Input(io.StringIO):
        def close(self):
            seen["input"] = self.getvalue()
            super().close()

    def fake_popen(cmd, **kw):
        capture(cmd, kw)
        events = TOOL_RUN if "--allowedTools" in cmd else [
            _text('{"content": ["x"]}'), {"type": "result", "is_error": False}]
        proc = _Proc(events)
        proc.stdin, proc.stderr = Input(), []
        return proc

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

    def call(stream, tools):
        result = provider.chat_completion(
            [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}],
            model="m", stream=stream, response_format={"type": "json_object"}, mcp_tools=tools)
        if stream:
            chunks = list(result)
            assert provider.is_stream_complete(chunks[-1])
            text = "".join(provider.get_streaming_content(c) or "" for c in chunks)
        else:
            text = provider.get_response_content(result)
        assert json.loads(text) == {"content": ["x"]}
        assert not seen["system_path"].exists()
        return seen

    return call


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("changes", [None, {}, {"chapter_number": None},
    {"url": "http://user:pass@localhost/mcp"}],
    ids=["disabled", "unreachable", "missing-chapter", "unsafe-url"])
def test_disabled_or_unavailable_research_still_translates(cli_call, monkeypatch, stream, changes):
    import providers.claude_code_provider as mod
    probes = []
    monkeypatch.setattr(mod, "_mcp_reachable", lambda url: probes.append(url) or False)
    tools = None if changes is None else {
        "url": "http://127.0.0.1:1/mcp", "book_id": 1, "chapter_number": 2, **changes}
    seen = cli_call(stream, tools)
    cmd = seen["cmd"]
    assert probes == ([tools["url"]] if changes == {} else [])
    assert "--allowedTools" not in cmd and "--max-turns" not in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert seen["system"] == "SYS"


@pytest.mark.parametrize("stream", [False, True])
def test_reachable_server_adds_tools(cli_call, monkeypatch, stream):
    import providers.claude_code_provider as mod
    monkeypatch.setattr(mod, "_mcp_reachable", lambda url: True)
    monkeypatch.setenv("CLAUDE_CODE_EFFORT", "high")
    tools = {"url": "http://127.0.0.1:8766/mcp", "book_id": 7,
             "chapter_number": 3, "max_turns": 5}
    seen = cli_call(stream, tools)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__t9"
    assert cmd[cmd.index("--max-turns") + 1] == "5"
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert "-p" in cmd and "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "m"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert ("--output-format" in cmd) is stream
    if stream:
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd and "--include-partial-messages" in cmd
    else:
        assert seen["kwargs"]["timeout"] == 1800
    assert "env" not in seen["kwargs"]  # inherit the user's CLI authentication
    assert json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["t9"] == {
        "type": "http", "url": tools["url"], "headers": {
            "X-T9-Caller": "translation", "X-T9-Book": "7", "X-T9-Chapter": "3"}}
    assert seen["system"].startswith("SYS") and "book_id=7" in seen["system"]
    assert "chapter 3" in seen["system"] and "If research is unavailable" in seen["system"]
    assert seen["input"].startswith("hi") and "valid JSON only" in seen["input"]


@pytest.mark.parametrize("name", ["cc", "claudecode"])
def test_factory_requires_no_api_key(monkeypatch, name):
    from providers.factory import ProviderFactory
    for key in ("ANTHROPIC_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_BIN", "/synthetic/claude")
    provider = ProviderFactory().create_provider(name)
    assert isinstance(provider, ClaudeCodeProvider)
    assert provider.bin_path == "/synthetic/claude"
