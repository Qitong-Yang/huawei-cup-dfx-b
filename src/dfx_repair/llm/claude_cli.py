"""Backend that runs one localization session through the local ``claude -p`` CLI.

The CLI runs the agent loop with built-in tools disabled; its only tools are ours, served by
``dfx_repair.llm.mcp_server``. This backend reads the ``stream-json`` events while the CLI
runs. It tallies tokens per API message, writes ``{"turns", "tokens"}`` for the server's
budget reminders, and stops the CLI once the answer is submitted (the CLI would otherwise make
one more call for a closing message) or once the case's token budget is used up. The first
valid submit is read from the answer file the server writes.

Linux/WSL only: on Windows the npm ``claude.cmd`` shim passes arguments through cmd.exe, which
cuts multi-line prompts, and killing it does not stop node. The submitted pipeline uses an
OpenAI-compatible backend instead (spec 7.4).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .session import SessionRequest, SessionResult

logger = logging.getLogger(__name__)

TOOL_NAMES = ("mcp__dfx__read_file", "mcp__dfx__search", "mcp__dfx__submit")
_SRC_DIR = Path(__file__).resolve().parents[2]
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")
_MODEL_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
                     "cacheCreationInputTokens")


def build_command(request: SessionRequest, mcp_config: Path,
                  executable: str = "claude") -> list[str]:
    cfg = request.config
    return [
        executable, "-p", request.user_prompt,
        "--model", cfg.model,
        "--system-prompt", request.system_prompt,
        "--tools", "",
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
        "--allowedTools", ",".join(TOOL_NAMES),
        "--setting-sources", "local",
        "--no-session-persistence",
        "--max-turns", str(cfg.max_turns),
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    ]


def mcp_config(request: SessionRequest, workdir: Path) -> dict:
    cfg = request.config
    args = [
        "-m", "dfx_repair.llm.mcp_server",
        "--app", str(request.app_root),
        "--answer", str(workdir / "answer.json"),
        "--state", str(workdir / "state.json"),
        "--max-turns", str(cfg.max_turns),
        "--token-budget", str(cfg.token_budget),
        "--warn-ratio", str(cfg.warn_ratio),
        "--read-limit", str(cfg.read_limit),
        "--search-limit", str(cfg.search_limit),
    ]
    return {"mcpServers": {"dfx": {"command": sys.executable, "args": args,
                                   "env": {"PYTHONPATH": str(_SRC_DIR)}}}}


class UsageTracker:
    """Token use per API message.

    ``message_start`` opens a call with its input usage and ``message_delta`` carries the
    call's final usage. ``assistant`` events only repeat the usage known when the message
    started (output tokens near zero), so they never replace a ``message_delta`` value.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict] = {}
        self._final: set[str] = set()
        self.current: str | None = None

    def feed(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "stream_event":
            inner = event.get("event") or {}
            if inner.get("type") == "message_start":
                message = inner.get("message") or {}
                self._set(message.get("id"), message.get("model"), message.get("usage"))
            elif inner.get("type") == "message_delta" and self.current in self._by_id:
                self._by_id[self.current]["usage"] = {**self._by_id[self.current]["usage"],
                                                      **(inner.get("usage") or {})}
                self._final.add(self.current)
        elif kind == "assistant":
            message = event.get("message") or {}
            if message.get("id") not in self._final:
                self._set(message.get("id"), message.get("model"), message.get("usage"))

    def _set(self, mid: str | None, model: str | None, usage: dict | None) -> None:
        if not mid:
            return
        self.current = mid
        old = self._by_id.get(mid, {})
        self._by_id[mid] = {"id": mid, "model": model or old.get("model"),
                            "usage": dict(usage or old.get("usage") or {})}

    @property
    def calls(self) -> list[dict]:
        return list(self._by_id.values())

    @property
    def turns(self) -> int:
        return len(self._by_id)

    @property
    def tokens(self) -> int:
        return sum(int(c["usage"].get(k) or 0) for c in self._by_id.values() for k in _USAGE_KEYS)

    @property
    def cache_read_tokens(self) -> int:
        return sum(int(c["usage"].get("cache_read_input_tokens") or 0)
                   for c in self._by_id.values())


@dataclass
class _Attempt:
    """What one CLI run produced."""

    answer: dict | None = None
    error: str | None = None
    retryable: bool = False
    over_budget: bool = False
    warned: bool = False
    tokens: int = 0
    cache_read_tokens: int = 0
    turns: int = 0
    stopped: str | None = None  # why this backend stopped the CLI: "submitted" | "budget"
    calls: list[dict] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    info: dict = field(default_factory=dict)


def _parse(line: str) -> dict | None:
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _write_state(path: Path, turns: int, tokens: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"turns": turns, "tokens": tokens}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # the server treats a missing state as zero; never fatal
        logger.warning("could not write state file: %s", exc)


def _tool_results(event: dict) -> list[dict]:
    content = (event.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
            and b.get("tool_use_id")]


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict)).strip()
    return str(content or "").strip()


def _init_problem(event: dict) -> str | None:
    tools = event.get("tools") or []
    if set(tools) != set(TOOL_NAMES):
        return f"unexpected tool set: {sorted(tools)}"
    servers = {s.get("name"): s.get("status") for s in event.get("mcp_servers") or []}
    if servers.get("dfx") != "connected":
        return "mcp server not connected"
    return None


class ClaudeCliBackend:
    name = "claude-cli"

    def __init__(self, executable: str = "claude") -> None:
        self.executable = executable

    def run(self, request: SessionRequest) -> SessionResult:
        cfg = request.config
        attempts: list[_Attempt] = []
        for _ in range(cfg.attempts):
            spent = sum(a.tokens for a in attempts)
            attempt = self._attempt(request, spent)
            attempts.append(attempt)
            if attempt.answer is not None or attempt.over_budget or not attempt.retryable:
                break
            logger.warning("%s: retrying after %s", request.case_id, attempt.error)
        last = attempts[-1]
        info: dict = {}
        for a in attempts:
            info.update(a.info)
        argv = build_command(request, Path("<mcp_config>"), self.executable)
        argv[argv.index("-p") + 1] = "<user_prompt>"
        argv[argv.index("--system-prompt") + 1] = "<system_prompt>"
        return SessionResult(
            answer=last.answer,
            error=None if last.answer is not None else last.error,
            over_budget=any(a.over_budget for a in attempts),
            budget_warned=any(a.warned for a in attempts),
            turns=sum(a.turns for a in attempts),
            tokens=sum(a.tokens for a in attempts),
            cache_read_tokens=sum(a.cache_read_tokens for a in attempts),
            seconds=round(sum(a.seconds for a in attempts), 2),
            calls=tuple(c for a in attempts for c in a.calls),
            transcript=tuple(e for a in attempts for e in a.transcript),
            meta={"backend": self.name, "attempts": len(attempts),
                  "cli_version": info.get("cli_version"), "num_turns": info.get("num_turns"),
                  "cost_usd": info.get("cost_usd"),
                  "permission_denials": info.get("permission_denials"), "argv": argv},
        )

    def _attempt(self, request: SessionRequest, spent: int) -> _Attempt:
        exe = shutil.which(self.executable)
        if exe is None:
            return _Attempt(error="claude CLI not found")
        with tempfile.TemporaryDirectory(prefix="dfx-llm-", ignore_cleanup_errors=True) as tmp:
            workdir = Path(tmp)
            config_path = workdir / "mcp.json"
            config_path.write_text(json.dumps(mcp_config(request, workdir)), encoding="utf-8")
            started = time.perf_counter()
            try:
                proc = subprocess.Popen(
                    build_command(request, config_path, exe), cwd=workdir,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return _Attempt(error="claude CLI not found")
            attempt = self._stream(proc, request, workdir, spent)
            attempt.seconds = time.perf_counter() - started
            answer_path = workdir / "answer.json"
            if answer_path.is_file():
                try:
                    attempt.answer = json.loads(answer_path.read_text(encoding="utf-8"))
                except ValueError:
                    logger.warning("%s: unreadable answer file", request.case_id)
        if attempt.answer is None and attempt.error is None:
            attempt.error = (f"over token budget {request.config.token_budget}"
                             if attempt.over_budget else "no submit")
        return attempt

    def _stream(self, proc: subprocess.Popen, request: SessionRequest, workdir: Path,
                spent: int) -> _Attempt:
        cfg = request.config
        out = _Attempt()
        tracker = UsageTracker()
        result_event: dict | None = None
        timed_out = threading.Event()
        stderr_parts: list[str] = []

        def on_timeout() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(cfg.timeout_s, on_timeout)
        reader = threading.Thread(target=lambda: stderr_parts.append(proc.stderr.read()),
                                  daemon=True)
        timer.start()
        reader.start()
        pending: set[str] = set()  # tool calls of the latest message without a result yet
        tool_names: dict[str, str] = {}
        stop_when_idle = False  # stop once every pending tool call has its result
        last_state: tuple[int, int] | None = None
        try:
            for line in proc.stdout:
                event = _parse(line)
                if event is None:
                    continue
                out.transcript.append(event)
                kind = event.get("type")
                if kind == "system" and event.get("subtype") == "init":
                    out.info["cli_version"] = event.get("claude_code_version")
                    problem = _init_problem(event)
                    if problem:
                        out.error = problem
                        proc.kill()
                        break
                    continue
                if kind == "result":
                    result_event = event
                    continue
                if kind == "user":
                    for block in _tool_results(event):
                        pending.discard(block["tool_use_id"])
                        if (tool_names.get(block["tool_use_id"]) == "mcp__dfx__submit"
                                and _result_text(block) == "submitted"):
                            out.stopped, stop_when_idle = "submitted", True
                    if stop_when_idle and not pending:
                        proc.kill()  # no further API call: answer given or budget used up
                        break
                    continue
                if kind not in ("assistant", "stream_event"):
                    continue
                known = tracker.current
                tracker.feed(event)
                if stop_when_idle and tracker.current != known:
                    proc.kill()  # a new call started; do not let it run
                    break
                if kind == "assistant":
                    for block in (event.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            pending.add(block["id"])
                            tool_names[block["id"]] = block.get("name", "")
                total = spent + tracker.tokens
                if (tracker.turns, total) != last_state:
                    last_state = (tracker.turns, total)
                    _write_state(workdir / "state.json", tracker.turns, total)
                if total >= cfg.warn_ratio * cfg.token_budget:
                    out.warned = True
                if total >= cfg.token_budget and not out.over_budget:
                    out.over_budget = stop_when_idle = True
                    out.stopped = out.stopped or "budget"
        finally:
            proc.wait()
            timer.cancel()
            reader.join(timeout=5)
            if proc.stdout:
                proc.stdout.close()
        self._finish(out, tracker, result_event, proc.returncode, timed_out.is_set(),
                     "".join(stderr_parts), cfg.timeout_s)
        return out

    @staticmethod
    def _finish(out: _Attempt, tracker: UsageTracker, result_event: dict | None,
                returncode: int, timed_out: bool, stderr: str, timeout_s: int) -> None:
        out.turns, out.calls = tracker.turns, tracker.calls
        out.tokens, out.cache_read_tokens = tracker.tokens, tracker.cache_read_tokens
        if result_event is not None:
            usage = (result_event.get("modelUsage") or {}).values()
            out.tokens = sum(int(u.get(k) or 0) for u in usage for k in _MODEL_USAGE_KEYS)
            out.cache_read_tokens = sum(int(u.get("cacheReadInputTokens") or 0) for u in usage)
            out.info.update(num_turns=result_event.get("num_turns"),
                            cost_usd=result_event.get("total_cost_usd"),
                            permission_denials=result_event.get("permission_denials"))
        if out.error is not None:  # init problem: not transient
            return
        if out.stopped and not timed_out:
            return  # this backend stopped the CLI on purpose; the exit code means nothing
        if timed_out:
            out.error, out.retryable = f"timeout {timeout_s}s", True
        elif result_event is not None and result_event.get("is_error"):
            out.error = str(result_event.get("result") or result_event.get("subtype"))[:300]
        elif result_event is None and returncode != 0 and not out.over_budget:
            out.error, out.retryable = f"claude exit {returncode}: {stderr.strip()[:300]}", True
