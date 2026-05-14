"""
Claude Code CLI provider.

Shells out to the local `claude` binary in `-p` (print) mode so that
translation jobs can ride on the user's Claude Code session/auth without
needing a separate API key.

The translation system prompt is delivered via `--system-prompt-file`, which
both passes it to the model as a true system message AND suppresses Claude
Code's default system prompt (CLAUDE.md auto-discovery, working-directory
info, env info, tool definitions, etc.). Tools are disabled with `--tools ""`
since translation never calls them. Sessions are not persisted
(`--no-session-persistence`) so they don't show up in `claude --resume`.

Streaming uses `--output-format stream-json --verbose
--include-partial-messages` so the translation engine's progress bar receives
real incremental chunks.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from typing import Dict, List, Optional, Any, Union

from .base import ModelProvider, StreamingResponse

logger = logging.getLogger(__name__)


_DEBUG_LOG_PATH = "/tmp/t9-claude-code-debug.log"


def _drain_stderr(proc: subprocess.Popen, sink: deque) -> threading.Thread:
    """Spawn a daemon thread that keeps the subprocess's stderr pipe drained,
    preventing deadlocks when the CLI emits more than ~64KB to stderr while
    we're blocked reading stdout. The last few KB are kept in `sink` so we
    can include them in error messages.

    When CLAUDE_CODE_DEBUG is set, every line is also appended to
    /tmp/t9-claude-code-debug.log with a timestamp + pid prefix so multiple
    concurrent calls don't interleave incomprehensibly.
    """
    debug = os.environ.get("CLAUDE_CODE_DEBUG")
    debug_fh = None
    if debug:
        try:
            debug_fh = open(_DEBUG_LOG_PATH, "a", encoding="utf-8")
            debug_fh.write(f"\n=== claude pid={proc.pid} starting ===\n")
            debug_fh.flush()
        except Exception:
            debug_fh = None

    def pump():
        import time as _time
        try:
            for line in proc.stderr:
                sink.append(line)
                if debug_fh is not None:
                    try:
                        debug_fh.write(f"[{_time.strftime('%H:%M:%S')} pid={proc.pid}] {line}")
                        debug_fh.flush()
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            if debug_fh is not None:
                try:
                    debug_fh.close()
                except Exception:
                    pass
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return t


class _Chunk:
    """Internal stream chunk recognized by get_streaming_content / is_stream_complete."""
    __slots__ = ("text", "done")

    def __init__(self, text: Optional[str] = None, done: bool = False):
        self.text = text
        self.done = done


class ClaudeCodeProvider(ModelProvider):
    """Provider that invokes the local `claude` CLI in print mode."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key or "", base_url, **kwargs)

        self.bin_path = (
            kwargs.get("bin_path")
            or os.environ.get("CLAUDE_CODE_BIN")
            or shutil.which("claude")
        )
        if not self.bin_path:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code or set CLAUDE_CODE_BIN."
            )

        self.timeout = kwargs.get("timeout", 1800)

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

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
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

        # Translation is a straightforward task — extended thinking would
        # burn minutes of latency for no quality gain, and our streaming
        # only forwards text_delta events (not thinking_delta), so a
        # high-effort run looks like an indefinite hang to the user.
        # Honors CLAUDE_CODE_EFFORT to override (low|medium|high|xhigh|max).
        effort = os.environ.get("CLAUDE_CODE_EFFORT", "medium")

        cmd = [
            self.bin_path, "-p",
            "--model", model,
            "--no-session-persistence",
            "--tools", "",
            "--effort", effort,
            # Disable MCP entirely. `--tools ""` blocks built-in tools but
            # not MCP servers — without this the CLI loads every MCP server
            # registered in the user's claude.ai account (Gmail, Calendar,
            # Drive, etc.) on every call, slowing startup and exposing the
            # translation to unrelated tools in the session init payload.
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
        ]
        if os.environ.get("CLAUDE_CODE_DEBUG"):
            # --debug writes to its own log destination, not stderr; use
            # --debug-file so we actually capture the output.
            cmd += ["--debug-file", "/tmp/t9-claude-code-debug.log"]

        sys_path = None
        if system_prompt:
            fd, sys_path = tempfile.mkstemp(suffix=".txt", prefix="t9-cc-sys-", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(system_prompt)
            except Exception:
                os.unlink(sys_path)
                raise
            cmd += ["--system-prompt-file", sys_path]

        logger.info(
            "claude CLI call: model=%s stream=%s json_mode=%s sys_chars=%d user_chars=%d timeout=%ss",
            model, stream, json_mode, len(system_prompt), len(user_prompt), self.timeout,
        )

        self._sweep_orphan_session_files()

        try:
            if stream:
                cmd += [
                    "--output-format", "stream-json",
                    "--verbose",
                    "--include-partial-messages",
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                # Drain stderr in a thread to prevent pipe-buffer deadlock.
                # `--verbose` (required by stream-json) can emit substantial
                # logs; if we never read them, claude blocks on the stderr
                # write while we're blocked reading stdout.
                stderr_buf: deque = deque(maxlen=200)
                _drain_stderr(proc, stderr_buf)
                try:
                    proc.stdin.write(user_prompt)
                    proc.stdin.close()
                except BrokenPipeError as e:
                    proc.kill()
                    self._unlink(sys_path)
                    raise RuntimeError(f"claude CLI stdin closed unexpectedly: {e}")
                # Ownership of sys_path passes to the iterator's finally block.
                return StreamingResponse(self._stream_iter(proc, sys_path, stderr_buf))

            try:
                try:
                    result = subprocess.run(
                        cmd,
                        input=user_prompt,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                except subprocess.TimeoutExpired as e:
                    captured = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
                    raise RuntimeError(
                        f"claude CLI timed out after {self.timeout}s. "
                        f"Last stderr: {captured.strip()[-2000:] or '(empty)'}"
                    )
            finally:
                self._unlink(sys_path)

            if result.returncode != 0:
                raise RuntimeError(
                    f"claude CLI failed (exit {result.returncode}): {result.stderr.strip()[-2000:]}"
                )
            content = result.stdout
            if json_mode:
                content = self._strip_markdown_fences(content)
            self._schedule_orphan_cleanup()
            return self._wrap_response(content, model)
        except Exception:
            # If anything blew up before we handed sys_path to the iterator, clean up.
            if not stream:
                self._unlink(sys_path)
            raise

    def _stream_iter(self, proc: subprocess.Popen, sys_path: Optional[str], stderr_buf: Optional[deque] = None):
        got_partial = False
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

                if etype == "stream_event":
                    inner = event.get("event", {})
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if text:
                                got_partial = True
                                yield _Chunk(text=text)
                elif etype == "assistant" and not got_partial:
                    msg = event.get("message", {})
                    blocks = msg.get("content", [])
                    if isinstance(blocks, list):
                        text = "".join(
                            b.get("text", "") for b in blocks
                            if b.get("type") == "text"
                        )
                        if text:
                            yield _Chunk(text=text)
                elif etype == "result":
                    # The CLI is done with the system-prompt file by now;
                    # unlink eagerly because the consumer typically breaks out
                    # of the loop on `done=True`, which would leave this
                    # generator suspended and its `finally` unrun until GC.
                    self._unlink(sys_path)
                    sys_path = None
                    yield _Chunk(done=True)
                    break
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            self._unlink(sys_path)
            self._schedule_orphan_cleanup()

        if proc.returncode not in (0, None):
            stderr = "".join(stderr_buf) if stderr_buf else ""
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): {stderr.strip()[-2000:]}"
            )

    @staticmethod
    def _unlink(path: Optional[str]) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _sweep_orphan_session_files() -> None:
        """Delete ai-title-only session files Claude Code writes as a
        background side effect of each --print call.

        Even with --no-session-persistence, the CLI fires off a background
        title-generation prompt whose result is written to
        ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl as a single ai-title
        line. They have no resume value but accumulate by the thousand and
        push real conversations out of `claude --resume`.

        Safety: only deletes files whose every line is an ai-title event,
        are <1KB, and haven't been modified in the last 30s (so an
        interactive session that just happens to have written its first
        line isn't caught).
        """
        if os.environ.get("CLAUDE_CODE_KEEP_SESSIONS"):
            return
        cwd = os.getcwd()
        encoded = "-" + cwd.lstrip("/").replace("/", "-")
        proj_dir = os.path.expanduser(f"~/.claude/projects/{encoded}")
        if not os.path.isdir(proj_dir):
            return
        cutoff = __import__("time").time() - 30
        try:
            entries = list(os.scandir(proj_dir))
        except OSError:
            return
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                st = entry.stat()
                if st.st_size > 1024 or st.st_mtime > cutoff:
                    continue
                with open(entry.path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            saw_line = False
            only_titles = True
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                saw_line = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    only_titles = False
                    break
                if obj.get("type") != "ai-title":
                    only_titles = False
                    break
            if saw_line and only_titles:
                try:
                    os.unlink(entry.path)
                except OSError:
                    pass

    def _schedule_orphan_cleanup(self) -> None:
        """Sweep ~35s after a call completes (gives Claude Code's background
        title-generation enough time to write its file plus the 30s mtime
        cutoff used by the sweep itself)."""
        if os.environ.get("CLAUDE_CODE_KEEP_SESSIONS"):
            return
        t = threading.Timer(35.0, self._sweep_orphan_session_files)
        t.daemon = True
        t.start()

    def _wrap_response(self, content: str, model: str) -> Dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"content": content, "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
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
        return "Claude Code CLI"

    @property
    def supported_features(self) -> List[str]:
        return ["streaming", "system_messages", "json_mode_via_prompt"]
