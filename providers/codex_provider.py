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
import subprocess
import tempfile
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


class CodexProvider(ModelProvider):
    """Provider that invokes the local `codex` CLI (`codex exec`)."""

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

    def _build_cmd(self, model: str, sys_path: Optional[str]) -> List[str]:
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
        system_prompt, user_prompt = self._split_messages(messages)
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

        cmd = self._build_cmd(model, sys_path)
        logger.info(
            "codex CLI call: model=%s stream=%s json_mode=%s sys_chars=%d user_chars=%d timeout=%ss",
            model, stream, json_mode, len(system_prompt), len(user_prompt), self.timeout,
        )

        handed_off = False  # sys_path ownership passed to the stream iterator
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            stderr_buf: deque = deque(maxlen=200)
            _drain_stderr(proc, stderr_buf)
            try:
                proc.stdin.write(user_prompt)
                proc.stdin.close()
            except BrokenPipeError as e:
                proc.kill()
                raise RuntimeError(f"codex CLI stdin closed unexpectedly: {e}")

            events = self._events(proc, stderr_buf, sys_path)
            handed_off = True
            if stream:
                return StreamingResponse(self._stream_iter(events, json_mode))

            text, usage = self._collect(events)
            if json_mode:
                text = self._strip_markdown_fences(text)
            return self._wrap_response(text, model, usage)
        finally:
            if not handed_off:
                self._unlink(sys_path)

    def _events(self, proc: subprocess.Popen, stderr_buf: deque, sys_path: Optional[str]):
        """Yield ("answer", text, usage) once the turn completes. Raises on a
        failed turn, mapping usage limits and OpenAI saturation onto the
        engine's pause/retry exceptions."""
        last_message = ""
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

                if etype == "item.completed" and item.get("type") == "agent_message":
                    # The last agent message of the turn is the answer.
                    last_message = item.get("text") or ""
                elif etype == "item.completed" and item.get("type") == "error":
                    # Non-fatal notices (e.g. an override this version doesn't
                    # know). Fatal ones arrive as `error` / `turn.failed`.
                    logger.debug("codex CLI notice: %s", item.get("message"))
                elif etype == "turn.completed":
                    self._unlink(sys_path)
                    sys_path = None
                    yield last_message, event.get("usage") or {}
                    return
                elif etype in ("turn.failed", "error"):
                    message = (event.get("error") or {}).get("message") or event.get("message") or ""
                    self._raise_failure(message)
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            self._unlink(sys_path)

        stderr = "".join(stderr_buf)
        self._raise_failure(stderr.strip()[-2000:] or f"codex CLI exited {proc.returncode} without a response")

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
