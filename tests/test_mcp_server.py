import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.llm_fakes import FULL_SUBMIT

from dfx_repair.llm.mcp_server import ServerContext, handle
from dfx_repair.llm.tools import AppTools

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def app(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "a").mkdir(parents=True)
    (root / "a" / "A.ets").write_text("let x = 1;\nuse(x);\n", encoding="utf-8")
    return root


@pytest.fixture
def ctx(app: Path, tmp_path: Path) -> ServerContext:
    return ServerContext(tools=AppTools(app), answer_path=tmp_path / "answer.json",
                         state_path=tmp_path / "state.json", max_turns=14,
                         token_budget=160_000, warn_ratio=0.8)


def call(ctx: ServerContext, name: str, args: dict) -> dict:
    msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": name, "arguments": args}}
    return handle(msg, ctx)["result"]


def text(result: dict) -> str:
    return result["content"][0]["text"]


def test_handshake_and_list(ctx):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"}}, ctx)
    assert r["id"] == 1 and r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "dfx"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx) is None
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)["result"]["tools"]
    assert [t["name"] for t in listed] == ["read_file", "search", "submit"]
    assert handle({"jsonrpc": "2.0", "id": 3, "method": "ping"}, ctx)["result"] == {}


def test_tools_run_inside_app(ctx):
    assert text(call(ctx, "read_file", {"path": "a/A.ets", "start_line": 2, "end_line": 2})) \
        == "2: use(x);"
    assert call(ctx, "read_file", {"path": "../x", "start_line": 1, "end_line": 1})["isError"]


def test_submit_first_valid_only(ctx):
    bad = call(ctx, "submit", {**FULL_SUBMIT, "hypothesis": "x"})
    assert bad["isError"] and not ctx.answer_path.exists()
    assert text(call(ctx, "submit", {**FULL_SUBMIT, "line": "7"})) == "submitted"
    again = call(ctx, "submit", {**FULL_SUBMIT, "line": 1})
    assert again["isError"] and text(again) == "error: already submitted"
    saved = json.loads(ctx.answer_path.read_text(encoding="utf-8"))
    assert saved["line"] == 7 and saved == {**FULL_SUBMIT, "line": 7}


def test_reminders_are_appended_once(ctx):
    ctx.state_path.write_text(json.dumps({"turns": 11, "tokens": 130_000}))
    first = text(call(ctx, "search", {"pattern": "x"}))
    assert "[budget] 3 turns left. Finish checking and submit soon." in first
    assert "[budget] Token budget almost used: call submit now with your best answer." in first
    assert "[budget]" not in text(call(ctx, "search", {"pattern": "x"}))
    ctx.state_path.write_text(json.dumps({"turns": 13, "tokens": 130_000}))
    final = text(call(ctx, "search", {"pattern": "x"}))
    assert final.endswith("[budget] Final turn: call submit now with your best answer.")


def test_unknown_method_and_garbage_state(ctx):
    assert handle({"jsonrpc": "2.0", "id": 9, "method": "nope"}, ctx)["error"]["code"] == -32601
    for garbage in ("{trunc", "[1]", '{"turns": "x"}'):
        ctx.state_path.write_text(garbage)
        r = call(ctx, "search", {"pattern": "x"})
        assert not r["isError"] and "[budget]" not in text(r)


def test_bad_tool_arguments_do_not_raise(ctx):
    r = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search"}},
               ctx)
    assert "result" in r
    assert call(ctx, "nope", {})["isError"]


def test_stdio_roundtrip(app, tmp_path):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "\n".join(json.dumps(m) for m in lines) + "\nnot json\n\n"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    proc = subprocess.run(
        [sys.executable, "-m", "dfx_repair.llm.mcp_server", "--app", str(app),
         "--answer", str(tmp_path / "answer.json"), "--state", str(tmp_path / "state.json"),
         "--max-turns", "14", "--token-budget", "160000", "--warn-ratio", "0.8",
         "--read-limit", "150", "--search-limit", "40"],
        input=stdin, capture_output=True, text=True, timeout=30, env=env, check=True)
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2]
