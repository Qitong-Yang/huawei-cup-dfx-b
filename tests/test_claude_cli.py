import json
import stat
import sys
from pathlib import Path

import pytest
from tests.llm_fakes import FULL_SUBMIT

from dfx_repair.llm import LlmConfig, SessionRequest
from dfx_repair.llm.claude_cli import (
    TOOL_NAMES,
    ClaudeCliBackend,
    UsageTracker,
    build_command,
    mcp_config,
)

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_claude.py"


def init(tools=TOOL_NAMES, status="connected") -> dict:
    return {"type": "system", "subtype": "init", "tools": list(tools),
            "mcp_servers": [{"name": "dfx", "status": status}],
            "claude_code_version": "2.1.309", "model": "claude-opus-5-5"}


def asst(mid: str, in_: int = 0, out: int = 0, cr: int = 0, cw: int = 0) -> dict:
    return {"type": "assistant", "message": {
        "id": mid, "model": "claude-opus-5-5", "role": "assistant", "content": [],
        "usage": {"input_tokens": in_, "output_tokens": out, "cache_read_input_tokens": cr,
                  "cache_creation_input_tokens": cw}}}


def user() -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def result(is_error: bool = False, text: str = "done", usage: dict | None = None) -> dict:
    return {"type": "result", "subtype": "error_during_execution" if is_error else "success",
            "is_error": is_error, "result": text, "num_turns": 2, "total_cost_usd": 0.05,
            "permission_denials": [],
            "modelUsage": usage if usage is not None else {"claude-opus-5-5": {
                "inputTokens": 2000, "outputTokens": 1000, "cacheReadInputTokens": 12_000,
                "cacheCreationInputTokens": 3000}}}


def start(mid: str, in_: int = 0, cr: int = 0, cw: int = 0) -> dict:
    return {"type": "stream_event", "event": {"type": "message_start", "message": {
        "id": mid, "model": "claude-opus-5-5", "usage": {
            "input_tokens": in_, "output_tokens": 1, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw}}}}


def delta(in_: int = 0, out: int = 0, cr: int = 0, cw: int = 0) -> dict:
    return {"type": "stream_event", "event": {"type": "message_delta", "usage": {
        "input_tokens": in_, "output_tokens": out, "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cw}}}


def tool_call(mid: str, *calls: tuple[str, str]) -> dict:
    event = asst(mid, 1, 1)
    event["message"]["content"] = [{"type": "tool_use", "id": tid, "name": f"mcp__dfx__{name}",
                                    "input": {}} for tid, name in calls]
    return event


def tool_result(tid: str, text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}]}]}}


OK_EVENTS = [init(), asst("m1", 1000, 500, 5000, 1500), user(),
             asst("m2", 1000, 500, 7000, 1500), result()]


@pytest.fixture
def request_(tmp_path: Path) -> SessionRequest:
    app = tmp_path / "app"
    app.mkdir()
    return SessionRequest("C1", app, "SYSTEM TEXT", "USER TEXT", LlmConfig())


class FakeRunner:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 request_: SessionRequest) -> None:
        self.tmp, self.request = tmp_path, request_
        self.exe = tmp_path / "claude"
        self.exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        self.script = tmp_path / "script.json"
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(self.script))

    def run(self, events=(), answer=None, budget=160_000, timeout_s=900, **extra):
        self.script.write_text(json.dumps({"events": list(events), "answer": answer, **extra}))
        cfg = LlmConfig(token_budget=budget, timeout_s=timeout_s)
        req = SessionRequest(self.request.case_id, self.request.app_root,
                             self.request.system_prompt, self.request.user_prompt, cfg)
        return ClaudeCliBackend(str(self.exe)).run(req)


@pytest.fixture
def fake(tmp_path, monkeypatch, request_) -> FakeRunner:
    return FakeRunner(tmp_path, monkeypatch, request_)


def test_command_has_no_builtin_tools_and_local_settings(request_):
    cmd = build_command(request_, Path("m.json"))
    assert cmd[:3] == ["claude", "-p", "USER TEXT"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--bare" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "local"
    assert cmd[cmd.index("--allowedTools") + 1] == ",".join(TOOL_NAMES)
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--max-turns") + 1] == "14"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--mcp-config") + 1] == "m.json"


def test_mcp_config_runs_our_server(request_, tmp_path):
    server = mcp_config(request_, tmp_path)["mcpServers"]["dfx"]
    assert server["command"] == sys.executable
    args = server["args"]
    assert args[:2] == ["-m", "dfx_repair.llm.mcp_server"]
    assert args[args.index("--app") + 1] == str(request_.app_root)
    assert args[args.index("--answer") + 1] == str(tmp_path / "answer.json")
    assert args[args.index("--token-budget") + 1] == "160000"


def test_tracker_dedupes_partial_usage():
    t = UsageTracker()
    t.feed(asst("m1", in_=2, out=2, cr=100, cw=50))
    t.feed(asst("m1", in_=2, out=40, cr=100, cw=50))
    t.feed(asst("m2", in_=1, out=10, cr=200, cw=0))
    assert (t.turns, t.tokens, t.cache_read_tokens) == (2, 403, 300)
    assert [c["id"] for c in t.calls] == ["m1", "m2"]


def test_tracker_takes_final_usage_from_message_delta():
    t = UsageTracker()
    t.feed(start("m1", in_=2, cr=100, cw=50))
    t.feed(asst("m1", in_=2, out=4, cr=100, cw=50))
    t.feed(delta(in_=2, out=900, cr=100, cw=50))
    t.feed(asst("m1", in_=2, out=4, cr=100, cw=50))  # a late snapshot must not undo the delta
    assert (t.turns, t.tokens, t.cache_read_tokens) == (1, 1052, 100)


def test_command_requests_partial_messages(request_):
    assert "--include-partial-messages" in build_command(request_, Path("m.json"))


def test_stops_after_successful_submit(fake):
    events = [init(), start("m1", 10, 100, 0), tool_call("m1", ("t1", "submit")),
              delta(10, 90, 100, 0), {"__write_answer__": True}, tool_result("t1", "submitted"),
              {"__sleep__": 5}, start("m2", 10, 200, 0), delta(10, 500, 200, 0), result()]
    r = fake.run(events=events, answer=FULL_SUBMIT)
    assert r.answer == FULL_SUBMIT and r.error is None and r.meta["attempts"] == 1
    assert (r.turns, r.tokens) == (1, 200) and r.seconds < 4


def test_invalid_submit_does_not_stop(fake):
    events = [init(), start("m1", 10), tool_call("m1", ("t1", "submit")), delta(10, 90),
              tool_result("t1", "error: missing fields: line"), start("m2", 10), delta(10, 40),
              result(usage={})]
    r = fake.run(events=events)
    assert r.turns == 2 and r.error == "no submit"


def test_crossing_call_runs_all_its_tools_before_stop(fake):
    events = [init(), start("m1", 10), tool_call("m1", ("t1", "search"), ("t2", "submit")),
              delta(600, 600), tool_result("t1", "hits"), {"__sleep__": 1.5},
              {"__write_answer__": True}, tool_result("t2", "submitted"), {"__sleep__": 5}]
    r = fake.run(events=events, answer=FULL_SUBMIT, budget=1000)
    assert r.over_budget and r.answer == FULL_SUBMIT and r.seconds < 5


def test_success_uses_result_model_usage(fake):
    r = fake.run(events=OK_EVENTS, answer=FULL_SUBMIT, argv_out=str(fake.tmp / "argv.json"))
    assert r.answer == FULL_SUBMIT and r.error is None and not r.over_budget
    assert (r.tokens, r.cache_read_tokens, r.turns) == (18_000, 12_000, 2)
    assert r.meta["cli_version"] == "2.1.309" and r.meta["cost_usd"] == 0.05
    assert r.meta["attempts"] == 1 and r.meta["permission_denials"] == []
    assert "<system_prompt>" in r.meta["argv"] and "SYSTEM TEXT" not in r.meta["argv"]
    assert len(r.transcript) == len(OK_EVENTS)
    assert json.loads((fake.tmp / "argv.json").read_text())[:2] == ["-p", "USER TEXT"]


def test_no_submit_is_an_error(fake):
    r = fake.run(events=OK_EVENTS)
    assert r.answer is None and r.error == "no submit"


def test_extra_tool_in_init_aborts_without_retry(fake):
    r = fake.run(events=[init(tools=[*TOOL_NAMES, "Read"])], sleep=5)
    assert r.answer is None and "unexpected tool set" in r.error and r.meta["attempts"] == 1


def test_disconnected_server_aborts(fake):
    r = fake.run(events=[init(status="failed")])
    assert r.error == "mcp server not connected" and r.meta["attempts"] == 1


def test_over_budget_kills_but_keeps_written_answer(fake):
    events = [init(), asst("m1", 600, 600), user(), asst("m2", 10, 10)]
    r = fake.run(events=events, answer=FULL_SUBMIT, budget=1000, sleep=5)
    assert r.over_budget and r.budget_warned and r.answer == FULL_SUBMIT
    assert r.tokens == 1200 and r.meta["attempts"] == 1 and r.seconds < 4


def test_over_budget_without_answer(fake):
    r = fake.run(events=[init(), asst("m1", 600, 600), user()], budget=1000, sleep=5)
    assert r.over_budget and r.answer is None and r.error == "over token budget 1000"


def test_truncated_last_line_is_ignored(fake):
    r = fake.run(events=OK_EVENTS, answer=FULL_SUBMIT, raw_tail='{"type": "assi', exit=1)
    assert r.answer == FULL_SUBMIT and r.tokens == 18_000


def test_not_logged_in_is_not_retried(fake):
    r = fake.run(events=[init(), result(is_error=True, text="Not logged in · Please run /login",
                                        usage={})], exit=1)
    assert r.error.startswith("Not logged in") and r.meta["attempts"] == 1


def test_crash_without_result_is_retried(fake):
    r = fake.run(events=[init()], exit=2, stderr="boom")
    assert r.error == "claude exit 2: boom" and r.meta["attempts"] == 2


def test_missing_cli(request_):
    assert ClaudeCliBackend("definitely-not-claude").run(request_).error == "claude CLI not found"


def test_timeout_retries_then_fails(fake):
    r = fake.run(events=[init()], sleep=5, timeout_s=1)
    assert r.error == "timeout 1s" and r.meta["attempts"] == 2 and r.seconds < 8


def test_stop_waits_for_the_final_usage_of_the_submit_call(fake):
    # Seen live: the CLI runs the tool before the call's message_delta arrives.
    events = [init(), start("m1", 10, 100, 0), tool_call("m1", ("t1", "submit")),
              {"__write_answer__": True}, tool_result("t1", "submitted"), {"__sleep__": 0.5},
              delta(10, 700, 100, 0), {"__sleep__": 5}]
    r = fake.run(events=events, answer=FULL_SUBMIT)
    assert r.tokens == 810 and r.seconds < 4 and r.error is None


def test_budget_crossed_before_tool_use_event_still_runs_that_tool(fake):
    # No stream events: the crossing is seen on one assistant event, the submit on the next.
    events = [init(), asst("m1", 600, 600), tool_call("m1", ("t1", "submit")),
              {"__sleep__": 1}, {"__write_answer__": True}, tool_result("t1", "submitted"),
              {"__sleep__": 5}]
    r = fake.run(events=events, answer=FULL_SUBMIT, budget=1000)
    assert r.over_budget and r.answer == FULL_SUBMIT and r.seconds < 5
