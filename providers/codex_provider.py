"""
Codex CLI provider.

Shells out to the local `codex` binary (`codex exec`) so that translation jobs
can ride on the user's ChatGPT/Codex login without an OpenAI API key — the
OpenAI counterpart of ClaudeCodeProvider.

Each call is one non-interactive, ephemeral `codex exec --json` turn:

- The translation system prompt replaces Codex's own base instructions via
  `model_instructions_file`, so the model is not told it is a coding agent.
- It runs in an empty scratch directory with a read-only sandbox,
  `--ignore-user-config` (no user MCP servers, profiles or model overrides)
  and `--ignore-rules`, so nothing from the host leaks into the prompt.
- The agent tooling translation never uses (shell, web search, apps, plugins,
  sub-agents, image tools, ...) is switched off with `-c features.X=false`.
  That cuts Codex's per-call overhead from ~13.6k to ~4k input tokens, which
  matters because every chunk is billed against the user's usage limit. The
  `-c` form is deliberate: an unknown key there is only a warning, whereas
  `--disable <unknown>` is a hard error, so a Codex release that renames a
  feature can't break translation.

`codex exec --json` reports the answer as a single completed item rather than
token deltas, so "streaming" here yields the whole response at the end of the
turn. Token usage, unlike the Claude Code CLI, is reported and passed through.
"""
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Union

from .base import (
    ModelProvider,
    StreamingResponse,
    OverloadedError,
    looks_overloaded,
    SessionLimitError,
    looks_session_limited,
)
from .claude_code_provider import _Chunk, _drain_stderr
from research_tools import (MCP_SERVER_NAME, RESEARCH_TOOLS, _mcp_reachable,
                            mcp_tools_section, valid_research_config)

logger = logging.getLogger(__name__)


# Features translation never needs. Turned off with `-c features.<name>=false`
# (see module docstring for why not --disable).
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "browser_use", "browser_use_external",
    "computer_use", "multi_agent", "goals", "image_generation", "view_image",
    "plugins", "remote_plugin", "skill_search", "tool_suggest", "sleep_tool",
)

_CONFIG_OVERRIDES = (
    'web_search="disabled"',
    "agents.enabled=false",  # 0.162.1 agent-tool switch; also retain the legacy feature flag
    "include_permissions_instructions=false",
    "include_apps_instructions=false",
    "include_environment_context=false",
    "project_doc_max_bytes=0",  # never read an AGENTS.md
)

# OpenAI-side saturation, as Codex reports it once its own retries run out.
# Only applied to Codex's error messages, never to model output.
_TRANSIENT_RE = re.compile(
    r"at capacity|overloaded|high demand|temporarily unavailable"
    r"|status:?\s*5\d\d|\b5\d\d\b.*(?:error|status)|stream disconnected",
    re.IGNORECASE,
)


# Workspace/credit exhaustion. Unlike the "hit your usage limit" notices these
# name no reset time — waiting only helps once someone adds credits — but
# pausing the queue still beats failing every remaining chapter. Matched only
# against Codex's error messages: base.looks_session_limited is also run over
# model output elsewhere, so this stays out of the shared pattern.
_CREDIT_LIMIT_RE = re.compile(
    r"out of credits|spend cap|reached your (?:usage|workspace credit) limit",
    re.IGNORECASE,
)


def _toml_str(value: str) -> str:
    """A TOML basic string for a `-c key=value` override."""
    return json.dumps(value)


class _CodexStream(StreamingResponse):
    """Own the process even before iteration, or while a reader is blocked."""

    def __init__(self, iterator, cleanup):
        super().__init__(iterator)
        self._cleanup = cleanup
        self.closed = False

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            chunk = super().__next__()
        except Exception:
            if self.closed:
                raise StopIteration
            raise
        if self.closed:
            raise StopIteration
        return chunk

    def close(self):
        self.closed = True
        self._cleanup()  # unblock stdout before trying to close its generator
        try:
            self.response_iterator.close()
        except ValueError:
            pass  # an active reader will finish and clean up when stdout closes

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class CodexProvider(ModelProvider):
    """Provider that invokes the local `codex` CLI (`codex exec`)."""

    supports_mcp_tools = True

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key or "", base_url, **kwargs)

        self.bin_path = (
            kwargs.get("bin_path")
            or os.environ.get("CODEX_BIN")
            or shutil.which("codex")
        )
        if not self.bin_path:
            raise RuntimeError("codex CLI not found. Install Codex or set CODEX_BIN.")

        self.timeout = kwargs.get("timeout", 1800)
        # An empty working root, so Codex has no project to look at.
        self.workdir = tempfile.mkdtemp(prefix="t9-codex-")

    def _split_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """Split messages into (system_prompt, user_prompt) strings."""
        sys_parts, user_parts = [], []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    item.get("text", "") for item in content
                    if item.get("type") == "text"
                )
            if msg.get("role") == "system":
                sys_parts.append(content)
            else:
                user_parts.append(content)
        return "\n\n".join(sys_parts), "\n\n".join(user_parts)

    def _build_cmd(self, model: str, sys_path: Optional[str], mcp_tools=None) -> List[str]:
        # Translation gains nothing from deep reasoning, and the response only
        # arrives at the end of the turn, so long thinking looks like a hang.
        # CODEX_EFFORT overrides (low|medium|high|xhigh|max).
        effort = os.environ.get("CODEX_EFFORT", "medium")
        cmd = [
            self.bin_path, "exec", "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox", "read-only",
            "--color", "never",
            "-C", self.workdir,
            "-m", model,
            "-c", f"model_reasoning_effort={_toml_str(effort)}",
        ]
        for override in _CONFIG_OVERRIDES:
            cmd += ["-c", override]
        for feature in _DISABLED_FEATURES:
            cmd += ["-c", f"features.{feature}=false"]
        if sys_path:
            cmd += ["-c", f"model_instructions_file={_toml_str(sys_path)}"]
        # Explicit overrides survive --ignore-user-config. Replace the entire
        # server map; never enable the user's servers, apps or plugins.
        servers = "{}"
        if mcp_tools:
            headers = {"X-T9-Caller": "translation", "X-T9-Book": str(mcp_tools["book_id"]),
                       "X-T9-Chapter": str(mcp_tools["chapter_number"])}
            header_toml = ", ".join(f"{_toml_str(k)}={_toml_str(v)}" for k, v in headers.items())
            servers = (f"{{{MCP_SERVER_NAME}={{url={_toml_str(mcp_tools['url'])}, "
                       f"http_headers={{{header_toml}}}, enabled_tools={json.dumps(RESEARCH_TOOLS)}, "
                       "required=false, startup_timeout_sec=2, tool_timeout_sec=15}}")
            cmd += ["-c", "mcp_optional_startup_grace_ms=0"]
            # Models using code mode can otherwise hide MCP behind an executor
            # or defer its catalog. Keep research calls visible and countable.
            cmd += ["-c", 'features.code_mode.direct_only_tool_namespaces=["mcp__t9"]']
        cmd += ["-c", f"mcp_servers={servers}", "-c", 'approval_policy="never"']
        cmd.append("-")  # prompt on stdin
        return cmd

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        thinking_effort: Optional[str] = None,  # absorbed; see CODEX_EFFORT
        **kwargs,
    ) -> Union[Dict[str, Any], StreamingResponse]:
        json_mode = bool(response_format and response_format.get("type") == "json_object")
        mcp_tools = kwargs.pop("mcp_tools", None)
        if mcp_tools and (not valid_research_config(mcp_tools)
                          or not _mcp_reachable(mcp_tools.get("url", ""))):
            logger.warning("codex CLI: research server unavailable or scope invalid — translating without tools")
            mcp_tools = None
        lookup_budget = None
        if mcp_tools:
            try:
                max_turns = max(2, int(mcp_tools.get("max_turns") or 8))
            except (TypeError, ValueError):
                max_turns = 8
            mcp_tools = {**mcp_tools, "max_turns": max_turns}
            lookup_budget = max_turns - 1
        system_prompt, user_prompt = self._split_messages(messages)
        if mcp_tools:
            system_prompt += mcp_tools_section(mcp_tools)
        if json_mode:
            user_prompt += (
                "\n\nIMPORTANT: You must respond with valid JSON only. "
                "Do not include any text before or after the JSON object. "
                "Do not wrap the JSON in markdown code fences."
            )

        sys_path = None
        if system_prompt:
            fd, sys_path = tempfile.mkstemp(suffix=".txt", prefix="t9-codex-sys-", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(system_prompt)
            except Exception:
                os.unlink(sys_path)
                raise

        cmd = self._build_cmd(model, sys_path, mcp_tools)
        logger.info(
            "codex CLI call: model=%s stream=%s json_mode=%s sys_chars=%d user_chars=%d timeout=%ss mcp=%s",
            model, stream, json_mode, len(system_prompt), len(user_prompt), self.timeout, bool(mcp_tools),
        )

        handed_off = False  # sys_path ownership passed to the stream iterator
        cleanup = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
            cleanup_lock = threading.Lock()
            cleaned = False

            def cleanup():
                nonlocal cleaned
                with cleanup_lock:
                    if cleaned:
                        return
                    cleaned = True
                    try:
                        self._stop_process(proc)
                    finally:
                        self._unlink(sys_path)

            stderr_buf: deque = deque(maxlen=200)
            _drain_stderr(proc, stderr_buf)
            try:
                proc.stdin.write(user_prompt)
                proc.stdin.close()
            except BrokenPipeError as e:
                raise RuntimeError(f"codex CLI stdin closed unexpectedly: {e}")

            events = self._events(proc, stderr_buf, cleanup, lookup_budget=lookup_budget)
            handed_off = True
            if stream:
                return _CodexStream(self._stream_iter(events, json_mode), cleanup)

            text, usage = self._collect(events)
            if json_mode:
                text = self._strip_markdown_fences(text)
            return self._wrap_response(text, model, usage)
        finally:
            if not handed_off:
                if cleanup is not None:
                    cleanup()
                else:
                    self._unlink(sys_path)

    def _events(self, proc: subprocess.Popen, stderr_buf: deque, cleanup,
                lookup_budget=None):
        """Yield ("answer", text, usage) once the turn completes. Raises on a
        failed turn, mapping usage limits and OpenAI saturation onto the
        engine's pause/retry exceptions."""
        last_message = ""
        lookups = set()
        answer = None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                item = event.get("item") or {}

                if item.get("type") == "mcp_tool_call" and etype in ("item.started", "item.completed"):
                    last_message = ""  # never return prose from a lookup turn
                    if lookup_budget is not None:
                        if item.get("server") != MCP_SERVER_NAME or item.get("tool") not in RESEARCH_TOOLS:
                            raise RuntimeError("codex CLI attempted an unconfigured research tool")
                        if item.get("id") or etype == "item.completed":
                            lookups.add(item.get("id") or f"call-{len(lookups)}")
                        if len(lookups) > lookup_budget:
                            raise RuntimeError("codex CLI research lookup budget exceeded")
                elif etype == "item.completed" and item.get("type") == "agent_message":
                    # The last agent message of the turn is the answer.
                    if item.get("phase") != "commentary":
                        last_message = item.get("text") or ""
                elif etype == "item.completed" and item.get("type") == "error":
                    # Non-fatal notices (e.g. an override this version doesn't
                    # know). Fatal ones arrive as `error` / `turn.failed`.
                    logger.debug("codex CLI notice: %s", item.get("message"))
                elif etype == "turn.completed":
                    if lookup_budget is not None and not last_message:
                        raise RuntimeError("codex CLI research turn completed without a final answer")
                    answer = (last_message, event.get("usage") or {})
                    # Let the CLI finish its normal shutdown, including any
                    # refreshed subscription credentials, before group cleanup.
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning("codex CLI did not exit within 5s after success; terminating")
                    break
                elif etype in ("turn.failed", "error"):
                    message = (event.get("error") or {}).get("message") or event.get("message") or ""
                    self._raise_failure(message)
        finally:
            # Reap before yielding even one chunk: a caller may stop at 'done',
            # and budget/error exits must not leave a CLI or its children alive.
            cleanup()

        if answer is not None:
            yield answer
            return
        stderr = "".join(stderr_buf)
        self._raise_failure(stderr.strip()[-2000:] or f"codex CLI exited {proc.returncode} without a response")

    @staticmethod
    def _stop_process(proc):
        def stop(sig):
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, sig)
                elif proc.poll() is None:
                    proc.kill()
            except ProcessLookupError:
                pass

        stop(signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            stop(getattr(signal, "SIGKILL", signal.SIGTERM))
            proc.wait(timeout=2)
        finally:
            # A wrapper can exit before its child. The whole isolated process
            # group belongs to this call, including children ignoring SIGTERM.
            if os.name == "posix":
                stop(signal.SIGKILL)

    @staticmethod
    def _raise_failure(message: str):
        if looks_session_limited(message) or _CREDIT_LIMIT_RE.search(message or ""):
            raise SessionLimitError(message.strip()[:300], reset_text=message)
        if looks_overloaded(message, strict=False) or _TRANSIENT_RE.search(message or ""):
            raise OverloadedError(message.strip()[:200] or "Codex service unavailable")
        raise RuntimeError(f"codex CLI failed: {message.strip()[-2000:]}")

    def _collect(self, events) -> tuple:
        for text, usage in events:
            return text, usage
        return "", {}

    def _stream_iter(self, events, json_mode: bool):
        for text, usage in events:
            if json_mode:
                text = self._strip_markdown_fences(text)
            if text:
                yield _Chunk(text=text)
            yield _Chunk(done=True)

    @staticmethod
    def _unlink(path: Optional[str]) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    def _wrap_response(self, content: str, model: str, usage: Dict[str, Any]) -> Dict[str, Any]:
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        return {
            "choices": [
                {
                    "message": {"content": content, "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
            "model": model,
        }

    def get_response_content(self, response: Dict[str, Any]) -> str:
        return response["choices"][0]["message"]["content"]

    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        if isinstance(chunk, _Chunk):
            return chunk.text
        return None

    def is_stream_complete(self, chunk: Any) -> bool:
        return isinstance(chunk, _Chunk) and chunk.done

    @property
    def provider_name(self) -> str:
        return "Codex CLI"

    @property
    def supported_features(self) -> List[str]:
        return ["streaming", "system_messages", "json_mode_via_prompt"]
