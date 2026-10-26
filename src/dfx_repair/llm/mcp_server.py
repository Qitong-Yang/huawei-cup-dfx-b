"""Stdio MCP server exposing read_file/search/submit for one case (spec 4.4, 4.8).

``claude -p`` starts this server and calls its tools. Messages are newline-delimited
JSON-RPC 2.0 on stdin/stdout; logs go to stderr only. The first valid ``submit`` is written
to the answer file. Budget reminders are appended to tool results, based on the
``{"turns", "tokens"}`` state file that the backend updates while the CLI runs.
Only the stdlib is used, so the server runs anywhere the pipeline runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from .tools import TOOL_SCHEMAS, AppTools, validate_submit

logger = logging.getLogger(__name__)

SERVER_NAME = "dfx"
DEFAULT_PROTOCOL = "2025-06-18"
TURNS_LEFT_3 = "3 turns left. Finish checking and submit soon."
FINAL_TURN = "Final turn: call submit now with your best answer."
TOKENS_LOW = "Token budget almost used: call submit now with your best answer."


@dataclass
class ServerContext:
    tools: AppTools
    answer_path: Path
    state_path: Path
    max_turns: int
    token_budget: int
    warn_ratio: float
    sent: set[str] = field(default_factory=set)
    submitted: bool = False


def _read_state(path: Path) -> dict:
    """The backend's {"turns", "tokens"}; a missing or half-written file counts as zero."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return {"turns": int(state.get("turns") or 0), "tokens": int(state.get("tokens") or 0)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"turns": 0, "tokens": 0}


def reminders(state: dict, ctx: ServerContext) -> list[str]:
    """Budget reminders not yet sent for this state; each text is sent at most once."""
    due: list[str] = []
    left = ctx.max_turns - state.get("turns", 0)
    if left <= 1:
        due.append(FINAL_TURN)
        ctx.sent.add(TURNS_LEFT_3)  # superseded
    elif left <= 3:
        due.append(TURNS_LEFT_3)
    if state.get("tokens", 0) >= ctx.warn_ratio * ctx.token_budget:
        due.append(TOKENS_LOW)
    due = [t for t in due if t not in ctx.sent]
    ctx.sent.update(due)
    return due


def _write_answer(path: Path, answer: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _submit(args: dict, ctx: ServerContext) -> tuple[str, bool]:
    if ctx.submitted:
        return "error: already submitted", True
    answer, error = validate_submit(args)
    if answer is None:
        return error or "error: invalid submit", True
    _write_answer(ctx.answer_path, answer)
    ctx.submitted = True
    return "submitted", False


def _call_tool(params: dict, ctx: ServerContext) -> dict:
    name = params.get("name", "")
    args = params.get("arguments") or {}
    try:
        if not isinstance(args, dict):
            raise TypeError("arguments must be an object")
        if name == "submit":
            out, is_error = _submit(args, ctx)
        else:
            out = ctx.tools.call(name, args)
            is_error = out.startswith("error:")
            notes = reminders(_read_state(ctx.state_path), ctx)
            if notes:
                out += "".join(f"\n\n[budget] {t}" for t in notes)
    except Exception as exc:  # a tool must never take the server down
        logger.exception("tool %s failed", name)
        out, is_error = f"error: {type(exc).__name__}: {exc}", True
    return {"content": [{"type": "text", "text": out}], "isError": is_error}


def handle(message: dict, ctx: ServerContext) -> dict | None:
    """Answer one JSON-RPC message; notifications (no id) get no response."""
    if "id" not in message:
        return None
    msg_id = message["id"]
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        result: dict = {
            "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }
    elif method == "tools/list":
        result = {"tools": list(TOOL_SCHEMAS)}
    elif method == "tools/call":
        result = _call_tool(params, ctx)
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--answer", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--max-turns", type=int, default=14)
    p.add_argument("--token-budget", type=int, default=160_000)
    p.add_argument("--warn-ratio", type=float, default=0.8)
    p.add_argument("--read-limit", type=int, default=150)
    p.add_argument("--search-limit", type=int, default=40)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    ctx = ServerContext(
        tools=AppTools(args.app, args.read_limit, args.search_limit),
        answer_path=args.answer, state_path=args.state, max_turns=args.max_turns,
        token_budget=args.token_budget, warn_ratio=args.warn_ratio)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            logger.warning("skipping non-JSON line")
            continue
        if not isinstance(message, dict):
            continue
        response = handle(message, ctx)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
