# LLM Localization via `claude -p` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add locator `rules+llm`. It runs the frozen v4 prompt on Claude Opus 5.5 through the local subscription (`claude -p`), using our own `read_file`/`search`/`submit` tools served over MCP. Its output goes through the spec's deterministic gate and is scored by `eval/run_baseline.py`.

**Architecture:** `claude -p` runs the agent loop. A stdio MCP server (`dfx_repair.llm.mcp_server`) executes the three tools inside the sandbox app root and records the submitted answer to a file. A backend (`ClaudeCliBackend`) launches the CLI and streams `stream-json` events. While events arrive, it tallies tokens and turns, writes them to a state file that the MCP server reads to add budget reminders to tool results, and kills the process at the token cap. The locator (`LlmLocator`) builds the prompt, calls any `Backend`, checks the quotes, and merges the result with the rule answer into a `LocateResult`. The OpenAI-compatible backend (OpenRouter) is **out of scope**. The `Backend` protocol is its later seam, and it is also the fake used in tests.

**Tech Stack:** Python ≥3.10 stdlib only (no `mcp` SDK; the project has `dependencies = []` and a teammate runs stdlib-only on Windows), pytest, ruff, Claude Code CLI 2.1.309.

**Spec:** `docs/superpowers/specs/2026-10-22-llm-localization-design.md` (sections 4.1–4.11, 7.1, 7.4).

## Global Constraints

- Prompt: the v4 `SYSTEM` text from `temp/spike_llm.py`, byte-identical; SHA-256 of the text (no trailing newline) = `12e096761c18ebfdf9828063944d583d7ef3ce5f0016614a3e075aa36c4c68fe`. `temp/` is untracked, so this hash is the only frozen reference. (Spec 4.5 also names `eval/runs/llm_spike_v4_sonnet_5case_20261022/system_prompt.txt`; that file is the Read/Grep rewrite and must **not** be used.)
- Defaults (spec 4.2/4.3/4.4/4.8): model `claude-opus-5-5`, 14 turns, 160 000 tokens per case, warning at 80 %, read ≤150 lines, search ≤40 hits, searchable suffixes `.ets .ts .js .json .json5`, crash log first 5000 chars, window crash−40 … crash+15.
- Token accounting = input + output + cache read + cache write, all attempts of a case.
- CLI flags fixed by the 2026-10-22 probe: `--setting-sources local` (keeps subscription login and does not load `~/.claude/CLAUDE.md`); **never `--bare`** (it fails with "Not logged in").
- Tools only see the sandbox `app/` dir: resolve paths, reject anything outside, skip `retrieve.source_map.SKIP_DIRS` and symlinks.
- No API key or token is written to any record.
- Files 200–400 lines max; type hints; module `logger = logging.getLogger(__name__)`; `__all__` in each `__init__.py`; ruff line length 100.
- Verification for every task: `uv run pytest -q` and `uv run ruff check src eval tests` both clean.
- Commits: Conventional Commits, scope `llm`/`locate`/`eval`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Model calls `submit` twice, or with `line` as a string ("48") or a missing field → first *valid* submit is kept, digit strings are coerced to int, invalid submits get an error result and do not count. (Task 2, Task 4)
2. `claude` missing from PATH or not logged in (`result.is_error`, "Not logged in") → case falls back to the rule answer with that error, and is not retried (an auth error is not transient). (Task 5)
3. Process killed mid-stream (budget/timeout) leaves a truncated last stdout line → parser skips non-JSON lines; an answer already written to `answer.json` is still read. (Task 5)
4. Quoted line in a CRLF or BOM-prefixed source file → quote check still matches (read with `utf-8-sig`, compare stripped). (Task 2)
5. Sandbox app contains a `testcases/` or `oh_modules/` folder or a symlink out of the app → neither `read_file` nor `search` returns its content. (Task 2)

---

### Task 1: Branch, prompt resource and session types

**Files:**
- Create: `src/dfx_repair/llm/prompts/localize_v4.txt`
- Create: `src/dfx_repair/llm/session.py`
- Modify: `src/dfx_repair/llm/__init__.py`
- Test: `tests/test_llm_session.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class LlmConfig:
      model: str = "claude-opus-5-5"; max_turns: int = 14; token_budget: int = 160_000
      warn_ratio: float = 0.8; timeout_s: int = 900; attempts: int = 2
      read_limit: int = 150; search_limit: int = 40; log_chars: int = 5000
      window_before: int = 40; window_after: int = 15
  @dataclass(frozen=True)
  class SessionRequest: case_id: str; app_root: Path; system_prompt: str; user_prompt: str; config: LlmConfig
  @dataclass(frozen=True)
  class SessionResult:
      answer: dict | None; error: str | None; over_budget: bool; budget_warned: bool
      turns: int; tokens: int; cache_read_tokens: int; seconds: float
      calls: tuple[dict, ...]       # one per API message: {"id", "model", "usage"}
      transcript: tuple[dict, ...]  # raw stream-json events, all attempts
      meta: dict                    # {"backend", "attempts", "cli_version", "num_turns", "cost_usd", "permission_denials", "argv"}
  class Backend(Protocol):
      name: str
      def run(self, request: SessionRequest) -> SessionResult: ...
  PROMPT_VERSION = "v4"
  def load_prompt(version: str = PROMPT_VERSION) -> tuple[str, str]  # (text, sha256 hex)
  ```

- [ ] **Step 1: Create the branch and commit the spec**

```bash
git switch -c feat/llm-locate
git add docs/superpowers/specs/2026-10-22-llm-localization-design.md docs/superpowers/plans/2026-10-22-llm-localization-claude-cli.md
git commit -m "docs(llm): add LLM localization spec and claude -p plan"
```

- [ ] **Step 2: Write the prompt file from the frozen source**

Run: `.venv/bin/python -I -c "import sys; sys.path[:0]=['src','.','temp']; import spike_llm as s; open('src/dfx_repair/llm/prompts/localize_v4.txt','w',encoding='utf-8',newline='\n').write(s.SYSTEM+'\n')"`

- [ ] **Step 3: Write the failing tests**

```python
def test_prompt_is_frozen_v4():
    text, sha = load_prompt()
    assert sha == "12e096761c18ebfdf9828063944d583d7ef3ce5f0016614a3e075aa36c4c68fe"
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    assert "Call submit exactly once." in text and not text.endswith("\n")

def test_unknown_prompt_version_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("v999")

def test_config_defaults_match_spec():
    c = LlmConfig()
    assert (c.max_turns, c.token_budget, c.warn_ratio, c.read_limit, c.search_limit) == (14, 160_000, 0.8, 150, 40)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.max_turns = 3
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_session.py -v` — Expected: FAIL (ImportError).

- [ ] **Step 5: Implement `session.py`**

`load_prompt` reads `prompts/localize_{version}.txt` via `importlib.resources.files("dfx_repair.llm")`, strips exactly one trailing `"\n"`, hashes the UTF-8 text. Export all names above from `llm/__init__.py`.

- [ ] **Step 6: Run tests, ruff — PASS. Commit** `feat(llm): add frozen v4 prompt and session types`

---

### Task 2: Sandbox-confined tools

**Files:**
- Create: `src/dfx_repair/llm/tools.py`
- Test: `tests/test_llm_tools.py`

**Interfaces:**
- Produces:
  ```python
  class AppTools:
      def __init__(self, app_root: Path, read_limit: int = 150, search_limit: int = 40) -> None
      def read_file(self, path: str, start_line: int, end_line: int) -> str
      def search(self, pattern: str, path_contains: str = "") -> str
      def line_text(self, path: str, line: int) -> str | None   # raw line, None if out of app/range
      def call(self, name: str, args: dict) -> str               # "read_file" | "search"; else "error: unknown tool"
  TOOL_SCHEMAS: tuple[dict, ...]  # MCP shape {"name","description","inputSchema"} for read_file, search, submit
  SUBMIT_FIELDS: tuple[str, ...]  # the 11 required fields of spec 4.6
  def validate_submit(args: dict) -> tuple[dict | None, str | None]  # (normalized answer, error text)
  ```
- Copy tool descriptions and `submit` properties from `temp/spike_llm.py` `TOOLS` (convert `parameters` → `inputSchema`). Copy result texts verbatim from `run_tool`: `"error: no such file (paths are relative to the app root)"`, `"error: range outside file"`, `"error: bad regex {exc}"`, `"no matches"`, `"(showing {k} of {n} hits; narrow the pattern or use path_contains)"`; hit format `"{rel}:{n}: {text.strip()[:160]}"`; numbered format `"{n}: {line}"`.

- [ ] **Step 1: Write the failing tests** (build a tiny app in `tmp_path`)

```python
def test_read_file_numbers_and_caps_lines(app):           # 300-line file
    out = AppTools(app).read_file("a/A.ets", 1, 300).splitlines()
    assert len(out) == 150 and out[0].startswith("1: ")

def test_paths_outside_app_are_rejected(app, tmp_path):   # ../, absolute, symlink to outside
    t = AppTools(app)
    for p in ("../secret.txt", str(tmp_path / "secret.txt"), "link/secret.txt"):
        assert t.read_file(p, 1, 5).startswith("error: no such file")
        assert t.line_text(p, 1) is None

def test_skip_dirs_are_invisible(app):                    # app/testcases/x.json, app/oh_modules/y.ets
    t = AppTools(app)
    assert t.read_file("testcases/x.json", 1, 5).startswith("error: no such file")
    assert "oh_modules" not in t.search("needle")

def test_search_is_case_insensitive_and_filtered(app):    # 'Author_2' in .ets, also in README.md
    hits = AppTools(app).search("author_2")
    assert "a/A.ets:" in hits and "README.md" not in hits

def test_search_caps_hits_with_note(app):                 # 45 matching lines
    assert AppTools(app).search("hit").splitlines()[-1] == \
        "(showing 40 of 45 hits; narrow the pattern or use path_contains)"

def test_bad_regex_and_unknown_tool_return_text(app):
    t = AppTools(app)
    assert t.search("(").startswith("error: bad regex")
    assert t.call("delete", {}) == "error: unknown tool"

def test_line_text_handles_crlf_and_bom(app):             # file written as b"\xef\xbb\xbfa\r\nb\r\n"
    assert AppTools(app).line_text("b/B.ets", 1) == "a"

def test_validate_submit_coerces_and_rejects():
    ok, err = validate_submit({**FULL_SUBMIT, "line": "48"})
    assert err is None and ok["line"] == 48
    assert validate_submit({**FULL_SUBMIT, "hypothesis": "maybe"})[1]
    assert "evidence_quote" in validate_submit({k: v for k, v in FULL_SUBMIT.items() if k != "evidence_quote"})[1]
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `tools.py`.** Index searchable files once in `__init__` (walk like `SourceIndex.__init__`: skip `SKIP_DIRS` and symlinks, keep only resolved paths inside the root). Read with `encoding="utf-8-sig", errors="replace"` and `splitlines()`. `validate_submit`: all `SUBMIT_FIELDS` present, `hypothesis ∈ {"crash_line","upstream"}`, the three line fields are int or digit strings (coerce), quotes and files are str.
- [ ] **Step 4: Run tests, ruff — PASS. Commit** `feat(llm): add sandbox-confined read_file/search/submit tools`

---

### Task 3: Multi-line statement span

**Files:**
- Modify: `src/dfx_repair/retrieve/code_context.py`, `src/dfx_repair/retrieve/__init__.py`
- Test: `tests/test_code_context.py`

**Interfaces:**
- Produces: `def statement_span(lines: list[str], line: int) -> tuple[int, int]` (1-based, inclusive; exported from `dfx_repair.retrieve`).

- [ ] **Step 1: Write the failing tests**

```python
def test_statement_span_call_arguments():
    assert statement_span(["foo(a,", "  b,", "  c);"], 2) == (1, 3)

def test_statement_span_method_chain():
    assert statement_span(["x", "  .then(y)", "  .catch(z);"], 3) == (1, 3)

def test_statement_span_single_line_and_parens_in_strings():
    assert statement_span(["let s = '(';", "use(s);"], 2) == (2, 2)
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement.** Mask with `mask_non_code("\n".join(lines))`, then count depth over `(`/`[` minus `)`/`]` only (braces open blocks, not statements), clamped at 0:

```python
start = line
while start > 1 and (depth_before[start] > 0 or masked[start - 1].lstrip().startswith(".")):
    start -= 1
end = line
while end < len(lines) and (depth_before[end + 1] > 0 or masked[end].lstrip().startswith(".")):
    end += 1
```
`depth_before[k]` = depth at the start of line k; `masked[k-1]` is line k.
- [ ] **Step 4: Run tests, ruff — PASS. Commit** `feat(retrieve): add multi-line statement span`

---

### Task 4: Stdio MCP server

**Files:**
- Create: `src/dfx_repair/llm/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `AppTools`, `TOOL_SCHEMAS`, `validate_submit` (Task 2).
- Produces:
  ```python
  SERVER_NAME = "dfx"
  @dataclass
  class ServerContext:
      tools: AppTools; answer_path: Path; state_path: Path
      max_turns: int; token_budget: int; warn_ratio: float
      sent: set[str] = field(default_factory=set); submitted: bool = False
  def handle(message: dict, ctx: ServerContext) -> dict | None   # JSON-RPC response, None for notifications
  def reminders(state: dict, ctx: ServerContext) -> list[str]     # state = {"turns": int, "tokens": int}
  def main(argv: list[str] | None = None) -> None
  # argv: --app --answer --state --max-turns --token-budget --warn-ratio --read-limit --search-limit
  ```
- Protocol (newline-delimited JSON-RPC 2.0 on stdin/stdout, logs to stderr only):
  `initialize` → `{"protocolVersion": <echo client's>, "capabilities": {"tools": {}}, "serverInfo": {"name": "dfx", "version": __version__}}`; `tools/list` → `{"tools": [...TOOL_SCHEMAS]}`; `tools/call` → `{"content": [{"type": "text", "text": ...}], "isError": bool}`; `ping` → `{}`; any message without `id` → `None`; unknown method with `id` → error `-32601`. A tool exception becomes `isError` text; the server never exits on bad input.
- `submit`: valid and first → write the normalized answer atomically (`tmp` + `os.replace`), set `submitted`, return `"submitted"`; invalid → `isError`, error text; after a valid submit → `isError`, `"error: already submitted"`.
- Reminders (texts verbatim from `temp/spike_llm.py`), each sent at most once, appended to the next tool result as `"\n\n[budget] " + text`. The state file may be missing or half-written; treat it as `{"turns": 0, "tokens": 0}`.
  - `max_turns - turns <= 3` → `"3 turns left. Finish checking and submit soon."`
  - `max_turns - turns <= 1` → `"Final turn: call submit now with your best answer."`
  - `tokens >= warn_ratio * token_budget` → `"Token budget almost used: call submit now with your best answer."`

- [ ] **Step 1: Write the failing tests**

```python
def test_handshake_and_list(ctx):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, ctx)
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx) is None
    names = [t["name"] for t in handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)["result"]["tools"]]
    assert names == ["read_file", "search", "submit"]

def test_submit_first_valid_only(ctx):
    bad = call(ctx, "submit", {**FULL_SUBMIT, "hypothesis": "x"});  assert bad["isError"]
    assert not ctx.answer_path.exists()
    assert text(call(ctx, "submit", FULL_SUBMIT)) == "submitted"
    assert call(ctx, "submit", {**FULL_SUBMIT, "line": 1})["isError"]
    assert json.loads(ctx.answer_path.read_text())["line"] == FULL_SUBMIT["line"]

def test_reminders_are_appended_once(ctx):
    ctx.state_path.write_text(json.dumps({"turns": 11, "tokens": 130_000}))
    first = text(call(ctx, "search", {"pattern": "x"}))
    assert "3 turns left" in first and "Token budget almost used" in first
    assert "[budget]" not in text(call(ctx, "search", {"pattern": "x"}))

def test_unknown_method_and_garbage_state(ctx):
    assert handle({"jsonrpc": "2.0", "id": 9, "method": "nope"}, ctx)["error"]["code"] == -32601
    ctx.state_path.write_text("{trunc");  assert not call(ctx, "search", {"pattern": "x"})["isError"]

def test_stdio_roundtrip(app, tmp_path):   # subprocess: sys.executable -m dfx_repair.llm.mcp_server ...
    # send initialize + tools/list lines, close stdin; assert two JSON responses with ids 1 and 2
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `mcp_server.py`** (`main` loops over `sys.stdin`, skips blank/non-JSON lines, writes `json.dumps(resp) + "\n"` and flushes).
- [ ] **Step 4: Run tests, ruff — PASS. Commit** `feat(llm): add stdio MCP server for localization tools`

---

### Task 5: `claude -p` backend

**Files:**
- Create: `src/dfx_repair/llm/claude_cli.py`
- Test: `tests/test_claude_cli.py`, `tests/fixtures/fake_claude.py`

**Interfaces:**
- Consumes: `SessionRequest`, `SessionResult`, `LlmConfig` (Task 1); `mcp_server` CLI args (Task 4).
- Produces:
  ```python
  TOOL_NAMES = ("mcp__dfx__read_file", "mcp__dfx__search", "mcp__dfx__submit")
  def build_command(request: SessionRequest, mcp_config: Path, executable: str = "claude") -> list[str]
  def mcp_config(request: SessionRequest, workdir: Path) -> dict
  class UsageTracker:
      def feed(self, event: dict) -> None   # assistant events; last usage per message.id wins
      tokens: int; cache_read_tokens: int; turns: int; calls: list[dict]
  class ClaudeCliBackend:
      name = "claude-cli"
      def __init__(self, executable: str = "claude") -> None
      def run(self, request: SessionRequest) -> SessionResult
  ```
- Command, exactly: `[exe, "-p", user_prompt, "--model", model, "--system-prompt", system_prompt, "--tools", "", "--strict-mcp-config", "--mcp-config", str(mcp_config), "--allowedTools", ",".join(TOOL_NAMES), "--setting-sources", "local", "--no-session-persistence", "--max-turns", str(max_turns), "--output-format", "stream-json", "--verbose"]`. `meta["argv"]` stores it with the two prompt values replaced by `"<user_prompt>"`/`"<system_prompt>"`.
- MCP config: `{"mcpServers": {"dfx": {"command": sys.executable, "args": ["-m", "dfx_repair.llm.mcp_server", "--app", app_root, "--answer", wd/"answer.json", "--state", wd/"state.json", ...limits from config]}}}`.
- One attempt: fresh `tempfile.TemporaryDirectory()` as `cwd` and `wd`. `Popen` with stdout piped, line by line. `threading.Timer(timeout_s, proc.kill)`. Skip lines that are not JSON objects. Rules:
  - First `system/init` event: `set(tools) != set(TOOL_NAMES)`, or server `dfx` not `"connected"` → kill, error `"unexpected tool set: …"` / `"mcp server not connected"`, **no retry**.
  - Every assistant event → tracker; when tokens change, write `{"turns", "tokens"}` to `state.json` atomically; `tokens >= 0.8 * budget` → `budget_warned`; `tokens >= budget` → kill, `over_budget = True`.
  - The `result` event is authoritative when present: `tokens` = sum of `inputTokens + outputTokens + cacheReadInputTokens + cacheCreationInputTokens` over `modelUsage`; `cache_read_tokens` likewise. Without it, use the tracker.
  - `answer` = `answer.json` contents if it exists, whether or not the process was killed.
  - Error: `result.is_error` → `result["result"][:300]`; non-zero exit with no result → `f"claude exit {code}: {stderr[:300]}"`; timeout → `f"timeout {timeout_s}s"`; `FileNotFoundError` on launch → `"claude CLI not found"`.
- Retry (up to `attempts`): only when there is no answer, not over budget, and the error is a timeout or a non-zero exit without a `result` event. Usage, transcript and seconds add up across attempts.
- `tests/fixtures/fake_claude.py`: reads `FAKE_CLAUDE_SCRIPT` (a JSON file path in env) listing events to print. It can also write a given answer to the `--answer` path parsed from `--mcp-config`, and can exit with a given code or sleep.

- [ ] **Step 1: Write the failing tests**

```python
def test_command_has_no_builtin_tools_and_local_settings(request_):
    cmd = build_command(request_, Path("m.json"))
    assert cmd[cmd.index("--tools") + 1] == "" and "--bare" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "local"
    assert cmd[cmd.index("--allowedTools") + 1] == ",".join(TOOL_NAMES)

def test_tracker_dedupes_partial_usage():
    t = UsageTracker()
    t.feed(asst("m1", in_=2, out=2, cr=100, cw=50)); t.feed(asst("m1", in_=2, out=40, cr=100, cw=50))
    t.feed(asst("m2", in_=1, out=10, cr=200, cw=0))
    assert (t.turns, t.tokens, t.cache_read_tokens) == (2, 405, 300)

def test_success_uses_result_model_usage(fake):           # init ok, 2 assistant events, result with modelUsage
    r = fake.run(events=OK_EVENTS, answer=FULL_SUBMIT)
    assert r.answer["line"] == FULL_SUBMIT["line"] and r.error is None and r.tokens == 18_000

def test_extra_tool_in_init_aborts_without_retry(fake):
    r = fake.run(events=[init(tools=[*TOOL_NAMES, "Read"])])
    assert r.answer is None and "unexpected tool set" in r.error and r.meta["attempts"] == 1

def test_over_budget_kills_but_keeps_written_answer(fake):  # budget 1000, assistant usage 1200, answer written first
    r = fake.run(events=BIG_EVENTS, answer=FULL_SUBMIT, budget=1000)
    assert r.over_budget and r.answer is not None

def test_truncated_last_line_is_ignored(fake):
    r = fake.run(raw_tail='{"type": "assi')
    assert r.error is None or "Expecting" not in r.error

def test_not_logged_in_is_not_retried(fake):
    r = fake.run(events=[init(), result(is_error=True, text="Not logged in · Please run /login")])
    assert r.error.startswith("Not logged in") and r.meta["attempts"] == 1

def test_missing_cli(request_):
    assert ClaudeCliBackend("definitely-not-claude").run(request_).error == "claude CLI not found"

def test_timeout_retries_then_fails(fake):                 # fake sleeps 5 s, timeout_s=1, attempts=2
    r = fake.run(sleep=5, timeout_s=1)
    assert r.error == "timeout 1s" and r.meta["attempts"] == 2
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `claude_cli.py`.** Store `cli_version` from the init event's `claude_code_version`, and `num_turns`, `total_cost_usd`, `permission_denials` from `result`.
- [ ] **Step 4: Run tests, ruff — PASS. Commit** `feat(llm): add claude -p backend with MCP tools and live token cap`

---

### Task 6: `rules+llm` locator

**Files:**
- Create: `src/dfx_repair/locate/llm.py`
- Modify: `src/dfx_repair/locate/__init__.py`
- Create: `tests/llm_fakes.py` — `FakeBackend(answer: dict | None = None, error: str | None = None, tokens: int = FAKE_TOKENS)`, `name = "fake"`, `run()` stores `self.request` and returns a `SessionResult` with `turns=3`, `cache_read_tokens=tokens // 2`, `seconds=1.0`; `FAKE_TOKENS = 12_000`. Task 7 imports it (`from tests.llm_fakes import FakeBackend, FAKE_TOKENS`).
- Test: `tests/test_llm_locate.py`

**Interfaces:**
- Consumes: `locate_with_rules`, `LocateResult`, `Location`; `AppTools` (Task 2); `statement_span` (Task 3); `Backend`, `LlmConfig`, `SessionRequest`, `SessionResult`, `load_prompt` (Task 1).
- Produces:
  ```python
  METHOD = "rules+llm"
  KEEP_OVER_BUDGET_ANSWER = True   # spec 7.1 still open: keep and mark; flip to drop
  def build_user_prompt(log_text: str, rule: LocateResult, tools: AppTools, cfg: LlmConfig) -> str
  def check_answer(answer: dict, tools: AppTools) -> dict[str, bool]   # quote_matches, evidence_matches, origin_matches
  def adopt(answer: dict, checks: dict[str, bool], rule: LocateResult, over_budget: bool) -> tuple[bool, str]  # (adopted, reason)
  class LlmLocator:
      def __init__(self, backend: Backend, config: LlmConfig = LlmConfig(), record_dir: Path | None = None) -> None
      def __call__(self, case_id: str, ctx: CrashContext, index: SourceIndex, source_hash: str = "") -> LocateResult
      stats: list[dict]  # per case: case_id, adopted, reason, tokens, cache_read_tokens, turns, seconds, over_budget, error
  ```
- `build_user_prompt`: same text as `initial_prompt` in `temp/spike_llm.py` (rule view keys, window, closing sentence `"Decide: is the crash line the root cause, or is the bad value/code produced upstream? Then submit."`).
- `check_answer` and `adopt` follow spec 4.6 exactly. "Answers the crash line" is decided by position `(file, line) == (rule.crash_location.file, rule.crash_location.start_line)`, never by `hypothesis`. Reasons: `"adopted"`, `"no answer: <error>"`, `"quote mismatch"`, `"evidence mismatch"`, `"over budget (answer dropped)"`.
- Sanitized log: `index.app_root.parent / "crash.log"` (sandbox layout `{app, crash.log, manifest.json}`, enforced by `eval/sandbox.py`); missing → `FileNotFoundError` (the runner records ERROR).
- Adopted result: `method=METHOD`, `status="CANDIDATE"`. `root_cause_locations` in order, deduped by `(file, start, end)`: LLM line (`root_cause`); its statement span if multi-line (`statement_span`); rule primary (`rule_answer`); crash line (`crash_line`). `evidence` = rule evidence + `llm quote`, `llm evidence`, `llm reasoning` (first 300 chars). `assumptions` = rule assumptions + `"llm answer adopted; quote checks prevent fabrication, not wrong answers"` (+ `"over token budget"` if so).
- Fallback result: rule result with `method=METHOD`, the rule's `status` and locations, and assumptions + `f"llm fallback: {reason}"`.
- Record `record_dir/<slug>.json` (slug = `re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)`, same rule as `eval.sandbox.case_slug`): `{case_id, prompt_version, prompt_sha256, backend, config, user_prompt, answer, checks, adopted, reason, session: asdict(result) minus transcript, transcript}`.

- [ ] **Step 1: Write the failing tests** (sandbox-shaped `tmp_path/case/{app,crash.log}`; `FakeBackend` returns a preset `SessionResult` and keeps the request)

```python
def test_adopts_upstream_with_valid_quotes(sandbox):
    loc = LlmLocator(FakeBackend(answer=UPSTREAM_OK))
    r = loc("C1", ctx, SourceIndex(sandbox / "app"))
    assert r.method == "rules+llm" and r.status == "CANDIDATE"
    assert (r.primary.file, r.primary.start_line) == ("a/Mock.ets", 11)
    assert [l.role for l in r.root_cause_locations][-2:] == ["rule_answer", "crash_line"]

def test_crash_line_answer_needs_no_evidence(sandbox):     # answer == rule crash line, evidence quote wrong
    assert loc_with(CRASH_LINE_BAD_EVIDENCE).primary.start_line == CRASH_LINE

def test_hypothesis_label_is_ignored(sandbox):             # says crash_line but points elsewhere, bad evidence
    r = loc_with({**UPSTREAM_OK, "hypothesis": "crash_line", "evidence_quote": "nope"})
    assert "llm fallback: evidence mismatch" in r.assumptions

def test_fabricated_quote_falls_back_to_rules(sandbox):
    r = loc_with({**UPSTREAM_OK, "quoted_line": "made up"})
    assert r.primary == rule_result.primary and "llm fallback: quote mismatch" in r.assumptions

def test_backend_error_keeps_rule_answer_and_status(sandbox):
    r = loc_with(None, error="timeout 900s")
    assert r.primary == rule_result.primary and r.status == rule_result.status

def test_multiline_answer_adds_statement_span(sandbox):
    assert any(l.role == "statement_span" and l.end_line > l.start_line for l in loc_with(MULTILINE).root_cause_locations)

def test_prompt_contains_log_rule_view_and_window(sandbox):
    fb = FakeBackend(answer=UPSTREAM_OK); LlmLocator(fb)("C1", ctx, SourceIndex(sandbox / "app"))
    p = fb.request.user_prompt
    assert p.startswith("Crash log (sanitized):") and '"crash_location"' in p and f"{CRASH_LINE}: " in p
    assert fb.request.system_prompt == load_prompt()[0]

def test_record_written_without_secrets(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SECRET")
    LlmLocator(FakeBackend(answer=UPSTREAM_OK), record_dir=tmp_path / "llm")("C@1", ctx, SourceIndex(sandbox / "app"))
    text = (tmp_path / "llm" / "C_1.json").read_text()
    assert "12e09676" in text and "SECRET" not in text
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement `locate/llm.py`; export `LlmLocator` from `dfx_repair.locate`.**
- [ ] **Step 4: Run tests, ruff — PASS. Commit** `feat(locate): add rules+llm locator with deterministic quote gate`

---

### Task 7: Evaluation integration

**Files:**
- Modify: `eval/run_baseline.py`, `eval/score.py:154-173`
- Test: `tests/test_evaluation_hardening.py` (add cases)

**Interfaces:**
- Consumes: `LlmLocator`, `METHOD` (Task 6); `ClaudeCliBackend` (Task 5); `LlmConfig`, `load_prompt` (Task 1).
- Produces: `def make_llm_backend() -> Backend` in `run_baseline` (returns `ClaudeCliBackend()`; tests monkeypatch it). `--method` choices = `sorted([*LOCATORS, "rules+llm"])`.
- When `method == "rules+llm"`: the locator is `LlmLocator(make_llm_backend(), LlmConfig(), run_dir / "llm")`. `config.json` gains `"llm": {"backend", "prompt_version", "prompt_sha256", **asdict(LlmConfig())}`. The summary sets `"llm_calls"` = sum of turns, `"tokens"` = sum of tokens, and `"llm": {"cases": locator.stats, "cache_read_ratio": cache_read/tokens or 0.0, "mean_seconds"}`. Other methods are unchanged (`0`/`0`, no `"llm"` key).
- `render_markdown`: when `summary.get("llm")`, append a section `## LLM 用量`. It has one line of totals (tokens, cache read ratio, mean seconds, prompt version + first 12 hex chars of the SHA) and a table `| 案例 | 采纳 | 原因 | 轮数 | tokens | 缓存读 | 耗时 s |`.

- [ ] **Step 1: Write the failing tests** (follow `test_case_failures_are_isolated_and_all_counted`: monkeypatch `build_sandbox`, plus `make_llm_backend` → `FakeBackend`)

```python
def test_rules_llm_run_records_usage_and_prompt_hash(tmp_path, monkeypatch):
    summary, run_dir = run_evaluation(..., method="rules+llm")
    assert summary["tokens"] == 2 * FAKE_TOKENS and summary["llm"]["cases"][0]["adopted"] is True
    cfg = json.loads((run_dir / "config.json").read_text())
    assert cfg["llm"]["prompt_sha256"].startswith("12e09676")
    assert "## LLM 用量" in (run_dir / "report.md").read_text() and (run_dir / "llm").is_dir()

def test_rules_llm_backend_failure_is_not_an_error_case(tmp_path, monkeypatch):   # FakeBackend(error="timeout")
    summary, _ = run_evaluation(..., method="rules+llm")
    assert summary["coverage"]["error"] == 0

def test_other_methods_have_no_llm_section(tmp_path, monkeypatch):
    summary, run_dir = run_evaluation(..., method="rules")
    assert "llm" not in summary and "LLM 用量" not in (run_dir / "report.md").read_text()
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement both modifications.**
- [ ] **Step 4: Run the full suite + ruff — PASS. Commit** `feat(eval): score rules+llm locator with LLM usage report`

---

### Task 8: Live smoke run and first official run

No new code. Uses the subscription quota, so **ask the user before Step 3**.

- [ ] **Step 1: Smoke on 2 cases.** `run_baseline` has no case filter, so write a scratch copy of `resources/ground_truth.json` that keeps only the `TouristAttraction1` and `HomeStay1.0.6` entries, then run `uv run python -X utf8 -m eval.run_baseline --method rules+llm --ground-truth <scratch copy>`. Expected in each `run_dir/llm/*.json`: init `tools` = the 3 `TOOL_NAMES`, `mcp_servers` `dfx` connected, `permission_denials == []`, `cli_version == "2.1.309"`, an answer present, and first-call input tokens far below the size of `~/.claude/CLAUDE.md` (CLAUDE.md not loaded).
- [ ] **Step 2: Fix anything the smoke run exposed** with a test first (Tasks 4–5 own the code), then rerun Step 1.
- [ ] **Step 3: Full run (25 cases), after user approval.** `uv run python -X utf8 -m eval.run_baseline --method rules+llm`. Report exact/range against rules (21/25, 23/25), Sonnet v3 (22/25, 23/25) and the Opus spike in `eval/runs/llm_spike_v4_opus_25case_20261022/` (same model and prompt, built-in tools); total tokens, cache-read ratio, mean s/case; fallback reasons.
- [ ] **Step 4: Record results.** Put the numbers and run id into spec section 7.2 and a dated line in `plan/task_plan.md` Status; commit `docs(llm): record first rules+llm run`.
