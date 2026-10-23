"""Backend that runs one localization session through the local ``claude -p`` CLI.

The CLI runs the agent loop with built-in tools disabled; its only tools are ours, served by
``dfx_repair.llm.mcp_server``. This backend reads the ``stream-json`` events while the CLI
runs. It tallies tokens per API message, writes ``{"turns", "tokens"}`` for the server's
budget reminders, and stops the CLI once the case's token budget is used up. The first valid
submit is read from the answer file the server writes, even when the CLI was stopped.
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
        "--output-format", "stream-json", "--verbose",
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
    """Token use per API message; partial usage of one message is replaced, not added."""

    def __init__(self) -> None:
        self._by_id: dict[str, dict] = {}

    def feed(self, event: dict) -> None:
        message = event.get("message") or {}
        mid = message.get("id")
        if not mid:
            return
        usage = message.get("usage") or {}
        self._by_id[mid] = {"id": mid, "model": message.get("model"), "usage": usage}

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
        with tempfile.TemporaryDirectory(prefix="dfx-llm-") as tmp:
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
        stop_at_next_call = False  # set when the budget is crossed; spec 4.8 checks before a call
        last_tokens = -1
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
                elif kind == "assistant":
                    mid = (event.get("message") or {}).get("id")
                    if stop_at_next_call and mid not in {c["id"] for c in tracker.calls}:
                        proc.kill()
                        break
                    tracker.feed(event)
                    total = spent + tracker.tokens
                    if total != last_tokens:
                        last_tokens = total
                        _write_state(workdir / "state.json", tracker.turns, total)
                    if total >= cfg.warn_ratio * cfg.token_budget:
                        out.warned = True
                    if total >= cfg.token_budget:
                        out.over_budget = stop_at_next_call = True
                elif kind == "user" and stop_at_next_call:
                    proc.kill()  # tools of the crossing call have run; no further call
                    break
                elif kind == "result":
                    result_event = event
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
        if timed_out:
            out.error, out.retryable = f"timeout {timeout_s}s", True
        elif result_event is not None and result_event.get("is_error"):
            out.error = str(result_event.get("result") or result_event.get("subtype"))[:300]
        elif result_event is None and returncode != 0 and not out.over_budget:
            out.error, out.retryable = f"claude exit {returncode}: {stderr.strip()[:300]}", True
