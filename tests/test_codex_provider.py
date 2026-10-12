"""CodexProvider against a stand-in `codex` executable (no network, no login).

The fake CLI records its argv and stdin, then prints the JSONL event stream
`codex exec --json` would, chosen by FAKE_CODEX_MODE.
"""
import json
import os
import stat
import sys

import pytest

from providers.base import OverloadedError, SessionLimitError
from providers.codex_provider import CodexProvider

FAKE = r'''#!PYTHON
import json, os, sys, time
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
prompt = sys.stdin.read()
argv = sys.argv[1:]
instr = None
for i, a in enumerate(argv):
    if a == "-c" and argv[i + 1].startswith("model_instructions_file="):
        instr_path = json.loads(argv[i + 1].split("=", 1)[1])
        instr = open(instr_path, encoding="utf-8").read()
with open(os.environ["FAKE_CODEX_LOG"], "w", encoding="utf-8") as f:
    json.dump({"argv": argv, "stdin": prompt, "instructions": instr, "pid": os.getpid()}, f)

def ev(**e):
    print(json.dumps(e), flush=True)

ev(type="thread.started", thread_id="t1")
ev(type="item.completed", item={"id": "item_0", "type": "error",
   "message": "Codex is ignoring 1 unrecognized configuration setting."})
ev(type="turn.started")
if mode in ("research", "mcp_failure", "budget"):
    ev(type="item.completed", item={"id": "a0", "type": "agent_message", "text": "Let me check."})
    for n in range(2 if mode == "budget" else 1):
        item = {"id": "lookup" + str(n), "type": "mcp_tool_call", "server": "t9",
                "tool": "t9_search_entities", "arguments": {"book_id": 7, "pattern": "张三"},
                "status": "failed" if mode == "mcp_failure" else "completed",
                "result": {"content": [{"type": "text", "text": "secret tool output"}]}}
        ev(type="item.started", item=item)
        ev(type="item.completed", item=item)
    if mode == "budget":
        time.sleep(60)  # the provider must terminate and reap this process
    ev(type="item.completed", item={"id": "r0", "type": "reasoning", "text": "private reasoning"})
    ev(type="item.completed", item={"id": "final", "type": "agent_message", "text": '{"content":["Translation"]}'})
    ev(type="turn.completed", usage={"input_tokens": 10, "output_tokens": 5})
elif mode == "init_failure":
    ev(type="item.completed", item={"id": "mcp-warning", "type": "error", "message": "MCP initialization failed"})
    ev(type="item.completed", item={"id": "final", "type": "agent_message", "text": '{"content":["Translation"]}'})
    ev(type="turn.completed", usage={})
elif mode == "no_answer":
    ev(type="item.completed", item={"id": "a0", "type": "agent_message", "text": "Let me check."})
    ev(type="item.completed", item={"id": "lookup", "type": "mcp_tool_call", "server": "t9", "tool": "t9_search_entities"})
    ev(type="turn.completed", usage={})
elif mode in ("ok", "auth_flush", "success_hanging"):
    ev(type="item.completed", item={"id": "item_1", "type": "agent_message", "text": "thinking aloud"})
    ev(type="item.completed", item={"id": "item_2", "type": "agent_message",
       "text": '```json\n{"title": "Chapter 1", "content": ["Zhang San entered the city gate."]}\n```'})
    ev(type="turn.completed", usage={"input_tokens": 4100, "cached_input_tokens": 3000, "output_tokens": 52})
    if mode == "auth_flush":
        time.sleep(0.25)
        with open(os.environ["FAKE_AUTH_FILE"], "w") as f:
            json.dump({"refreshed": True}, f)
            f.flush()
            os.fsync(f.fileno())
    elif mode == "success_hanging":
        time.sleep(60)
elif mode == "limit":
    ev(type="error", message="You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
       "to purchase more credits or try again at 3:45 PM.")
    ev(type="turn.failed", error={"message": "You've hit your usage limit. Visit https://chatgpt.com/codex/"
       "settings/usage to purchase more credits or try again at 3:45 PM."})
    sys.exit(1)
elif mode == "credits":
    ev(type="turn.failed", error={"message": "Your workspace is out of credits. Add credits to continue using Codex."})
    sys.exit(1)
elif mode == "capacity":
    ev(type="turn.failed", error={"message": "exceeded retry limit, last status: 503 Service Unavailable"})
    sys.exit(1)
elif mode == "broken":
    ev(type="turn.failed", error={"message": "The model `nope` does not exist"})
    sys.exit(1)
elif mode == "crash":
    sys.stderr.write("panic: something went wrong\n")
    sys.exit(101)
elif mode == "hanging":
    ev(type="item.started", item={"id": "lookup", "type": "mcp_tool_call", "server": "t9", "tool": "t9_search_entities"})
    time.sleep(60)
'''.replace("PYTHON", sys.executable)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "codex"
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    provider = CodexProvider(bin_path=str(path))

    def call(mode="ok", stream=False, json_mode=True, consume=True, **kwargs):
        monkeypatch.setenv("FAKE_CODEX_MODE", mode)
        messages = [{"role": "system", "content": "SYSTEM: translate zh->en"},
                    {"role": "user", "content": "第一章\n张三走进了城门。"}]
        rf = {"type": "json_object"} if json_mode else None
        resp = provider.chat_completion(messages, model="gpt-6-luna", stream=stream, response_format=rf, **kwargs)
        if not consume:
            return resp
        if stream:
            chunks = list(resp)
            text = "".join(c for c in (provider.get_streaming_content(ch) for ch in chunks) if c)
            assert provider.is_stream_complete(chunks[-1])
            return text, None
        return provider.get_response_content(resp), resp

    call.log = lambda: json.loads(log.read_text())
    return call


def _sys_tempfiles():
    import tempfile
    d = tempfile.gettempdir()
    return {f for f in os.listdir(d) if f.startswith("t9-codex-sys-")}


def test_returns_last_agent_message_with_fences_stripped_and_usage(fake):
    text, resp = fake("ok")
    assert json.loads(text)["content"] == ["Zhang San entered the city gate."]
    assert resp["usage"] == {"prompt_tokens": 4100, "completion_tokens": 52, "total_tokens": 4152}


def test_streaming_yields_the_answer_then_done(fake):
    text, _ = fake("ok", stream=True)
    assert json.loads(text)["title"] == "Chapter 1"


def test_system_prompt_replaces_instructions_and_user_prompt_goes_to_stdin(fake):
    fake("ok")
    call = fake.log()
    assert call["instructions"] == "SYSTEM: translate zh->en"
    assert call["stdin"].startswith("第一章\n张三走进了城门。")
    assert "valid JSON only" in call["stdin"]
    argv = call["argv"]
    assert argv[:2] == ["exec", "--json"]
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("-m") + 1] == "gpt-6-luna"
    assert argv[-1] == "-"


def test_features_are_disabled_via_config_not_disable_flag(fake):
    # --disable <unknown> is a hard error in codex; -c features.X=false only warns.
    fake("ok")
    argv = fake.log()["argv"]
    assert "--disable" not in argv
    assert "features.shell_tool=false" in argv
    assert 'web_search="disabled"' in argv


def test_system_prompt_tempfile_is_removed(fake):
    before = _sys_tempfiles()
    fake("ok")
    fake("ok", stream=True)
    with pytest.raises(RuntimeError):
        fake("broken")
    assert _sys_tempfiles() <= before


def test_usage_limit_raises_session_limit_with_reset_text(fake):
    with pytest.raises(SessionLimitError) as exc:
        fake("limit")
    assert "try again at 3:45 PM" in exc.value.reset_text


def test_usage_limit_while_streaming(fake):
    with pytest.raises(SessionLimitError):
        fake("limit", stream=True)


def test_out_of_credits_pauses_instead_of_failing(fake):
    with pytest.raises(SessionLimitError):
        fake("credits")


def test_capacity_error_is_retryable_overload(fake):
    with pytest.raises(OverloadedError):
        fake("capacity")


def test_other_failures_are_plain_errors(fake):
    with pytest.raises(RuntimeError, match="does not exist"):
        fake("broken")


def test_crash_without_events_reports_stderr(fake):
    with pytest.raises(RuntimeError, match="panic: something went wrong"):
        fake("crash")


def test_missing_binary_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="codex CLI not found"):
        CodexProvider()


def test_factory_resolves_codex_and_alias():
    from providers.factory import ProviderFactory
    f = ProviderFactory()
    assert f._resolve_provider_name("cx") == "codex"
    assert f.config["providers"]["codex"]["class"] == "CodexProvider"
    assert "api_key_env" not in f.config["providers"]["codex"]


TOOLS = {"url": "http://127.0.0.1:8766/mcp", "book_id": 7,
         "book_title": "Synthetic", "chapter_number": 3, "max_turns": 2}


@pytest.mark.parametrize("stream", [False, True])
def test_disabled_research_config_is_empty(fake, monkeypatch, stream):
    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: pytest.fail("unexpected probe"))
    text, _ = fake(stream=stream)
    assert json.loads(text)["content"] == ["Zhang San entered the city gate."]
    assert "mcp_servers={}" in fake.log()["argv"]
    assert "RESEARCH TOOLS" not in fake.log()["instructions"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["research", "mcp_failure", "init_failure"])
def test_research_filters_events_and_preserves_isolation(fake, monkeypatch, stream, mode):
    try:
        import tomllib
    except ImportError:
        tomllib = pytest.importorskip("tomli", reason="TOML assertions need tomli on Python 3.10")
    from research_tools import RESEARCH_TOOLS
    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: True)
    text, _ = fake(mode, stream=stream, mcp_tools=TOOLS)
    assert json.loads(text) == {"content": ["Translation"]}
    logged = fake.log()
    argv = logged["argv"]
    overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
    servers = tomllib.loads(next(s for s in overrides if s.startswith("mcp_servers=")))["mcp_servers"]
    assert 'features.code_mode.direct_only_tool_namespaces=["mcp__t9"]' in overrides
    assert set(servers) == {"t9"}
    assert servers["t9"]["http_headers"] == {"X-T9-Caller": "translation", "X-T9-Book": "7", "X-T9-Chapter": "3"}
    assert set(servers["t9"]["enabled_tools"]) == set(RESEARCH_TOOLS)
    assert servers["t9"]["required"] is False
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    for feature in ("shell_tool", "apps", "plugins", "multi_agent"):
        assert f"features.{feature}=false" in overrides
    assert 'approval_policy="never"' in overrides
    assert 'agents.enabled=false' in overrides
    assert 'web_search="disabled"' in overrides
    assert "book_id=7" in logged["instructions"] and "chapter 3" in logged["instructions"]


@pytest.mark.parametrize("scope", [{}, {"chapter_number": None}, {"book_id": False}, {"chapter_number": 1.5}])
def test_unavailable_or_invalid_research_falls_back(fake, monkeypatch, scope):
    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: False)
    fake(mcp_tools={**TOOLS, **scope})
    assert "mcp_servers={}" in fake.log()["argv"]
    assert "RESEARCH TOOLS" not in fake.log()["instructions"]


@pytest.mark.parametrize("stream", [False, True])
def test_budget_failure_reaps_process_and_removes_prompt(fake, monkeypatch, stream):
    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: True)
    before = _sys_tempfiles()
    with pytest.raises(RuntimeError, match="lookup budget exceeded"):
        fake("budget", stream=stream, mcp_tools=TOOLS)
    with pytest.raises(ProcessLookupError):
        os.kill(fake.log()["pid"], 0)
    assert _sys_tempfiles() <= before
    # The next chunk/job can use the provider after this ordinary retryable error.
    text, _ = fake("research", stream=stream, mcp_tools=TOOLS)
    assert json.loads(text)["content"] == ["Translation"]


def test_tool_turn_without_final_answer_is_not_translation(fake, monkeypatch):
    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: True)
    with pytest.raises(RuntimeError, match="without a final answer"):
        fake("no_answer", mcp_tools=TOOLS)


@pytest.mark.parametrize("when", ["before_iteration", "blocked_reader", "after_answer", "abandoned"])
def test_stream_disconnect_reaps_codex_and_removes_prompt(fake, monkeypatch, when):
    import gc
    import threading
    import time
    import weakref

    monkeypatch.setattr("providers.codex_provider._mcp_reachable", lambda url: True)
    before = _sys_tempfiles()
    entered = threading.Event()
    if when == "blocked_reader":
        original = CodexProvider._events

        def observe(self, *args, **kwargs):
            entered.set()
            yield from original(self, *args, **kwargs)

        monkeypatch.setattr(CodexProvider, "_events", observe)
    stream = fake("ok" if when == "after_answer" else "hanging", stream=True,
                  consume=False, mcp_tools=TOOLS)
    deadline = time.monotonic() + 3
    while True:
        try:
            pid = fake.log()["pid"]
            break
        except (FileNotFoundError, json.JSONDecodeError):
            assert time.monotonic() < deadline, "fake CLI did not start"
            time.sleep(0.01)
    received, errors = [], []
    reader = None
    if when == "blocked_reader":
        def consume(response):
            try:
                received.extend(response)
            except Exception as exc:
                errors.append(exc)
        reader = threading.Thread(target=consume, args=(stream,), daemon=True)
        reader.start()
        assert entered.wait(timeout=2), "stream reader did not enter the event loop"
    elif when == "after_answer":
        assert next(stream).text
    if when == "abandoned":
        ref = weakref.ref(stream)
        del stream
        gc.collect()
        assert ref() is None
    else:
        stream.close()
        stream.close()  # repeated disconnect/cleanup notifications are harmless
    if reader:
        reader.join(timeout=3)
        assert not reader.is_alive()
        assert not received and not errors
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert _sys_tempfiles() <= before


@pytest.mark.parametrize("stream", [False, True])
def test_success_allows_auth_persistence_after_turn_completed(fake, monkeypatch, tmp_path, stream):
    auth = tmp_path / "synthetic-auth.json"
    monkeypatch.setenv("FAKE_AUTH_FILE", str(auth))
    fake("auth_flush", stream=stream)
    assert json.loads(auth.read_text()) == {"refreshed": True}
    with pytest.raises(ProcessLookupError):
        os.kill(fake.log()["pid"], 0)


def test_success_shutdown_timeout_still_reaps_process(fake, caplog):
    fake("success_hanging")
    assert "did not exit within 5s after success" in caplog.text
    with pytest.raises(ProcessLookupError):
        os.kill(fake.log()["pid"], 0)
