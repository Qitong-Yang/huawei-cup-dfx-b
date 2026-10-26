"""``rules+llm`` locator: the rule layer's answer, re-judged by an LLM session (spec 4.6, 4.7).

The LLM sees the sanitized crash log, the rule result and the code around the crash, then
searches the app with its tools and submits one line. Deterministic quote checks decide
whether that answer replaces the rule answer. They prevent fabricated quotes, not wrong
answers. Every case keeps the rule result as a fallback and is recorded for the report.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from dataclasses import asdict, replace
from pathlib import Path

from dfx_repair.ingest import CrashContext
from dfx_repair.llm import (
    PROMPT_VERSION,
    Backend,
    LlmConfig,
    SessionRequest,
    SessionResult,
    load_prompt,
)
from dfx_repair.llm.tools import AppTools
from dfx_repair.retrieve import SourceIndex, statement_span

from .result import LocateResult, Location
from .rules import locate_with_rules

logger = logging.getLogger(__name__)

METHOD = "rules+llm"
KEEP_OVER_BUDGET_ANSWER = True  # spec 7.1 still open: keep and mark; set False to drop
RULE_VIEW_KEYS = ("status", "crash_location", "root_cause_locations", "evidence",
                  "assumptions", "context_requests", "unresolved_candidates")
CLOSING = ("Decide: is the crash line the root cause, or is the bad value/code produced "
           "upstream? Then submit.")


def build_user_prompt(log_text: str, rule: LocateResult, tools: AppTools, cfg: LlmConfig) -> str:
    """Same text as the v4 spike's first message: log, rule view, numbered crash window."""
    crash = rule.crash_location
    window = ""
    if crash is not None:
        code = tools.read_file(crash.file, crash.start_line - cfg.window_before,
                               crash.start_line + cfg.window_after)
        if not code.startswith("error:"):
            window = f"\n\nCode around the crash ({crash.file}):\n{code}"
    data = rule.to_dict()
    view = {k: data[k] for k in RULE_VIEW_KEYS}
    return (f"Crash log (sanitized):\n{log_text[:cfg.log_chars]}\n\n"
            f"Rule layer output:\n{json.dumps(view, ensure_ascii=False, indent=1)}"
            f"{window}\n\n{CLOSING}")


def _quote_ok(tools: AppTools, file: str, line: int, quote: str) -> bool:
    text = tools.line_text(file, line)
    return text is not None and text.strip() == str(quote).strip()


def check_answer(answer: dict, tools: AppTools) -> dict[str, bool]:
    """Spec 4.6 checks: quoted lines must exist verbatim; evidence must be another line."""
    a = answer
    return {
        "quote_matches": _quote_ok(tools, a["file"], a["line"], a["quoted_line"]),
        "evidence_matches": (a["evidence_file"], a["evidence_line"]) != (a["file"], a["line"])
        and _quote_ok(tools, a["evidence_file"], a["evidence_line"], a["evidence_quote"]),
        "origin_matches": _quote_ok(tools, a["origin_file"], a["origin_line"], a["origin_quote"]),
    }


def _crash_line(rule: LocateResult) -> Location | None:
    """The aligned crash statement if the rules resolved it, else the logged position."""
    if rule.status == "CANDIDATE" and rule.root_cause_locations:
        return rule.root_cause_locations[-1]
    return rule.crash_location


def _is_crash_line(answer: dict, rule: LocateResult) -> bool:
    spots = {(loc.file, loc.start_line) for loc in (rule.crash_location, _crash_line(rule)) if loc}
    return (answer["file"], answer["line"]) in spots


def adopt(answer: dict, checks: dict[str, bool], rule: LocateResult, over_budget: bool,
          keep_over_budget: bool = KEEP_OVER_BUDGET_ANSWER) -> tuple[bool, str]:
    """Spec 4.6 gate. "Answers the crash line" is judged by position, never by hypothesis."""
    if over_budget and not keep_over_budget:
        return False, "over budget (answer dropped)"
    if not checks.get("quote_matches"):
        return False, "quote mismatch"
    if not _is_crash_line(answer, rule) and not checks.get("evidence_matches"):
        return False, "evidence mismatch"
    return True, "adopted"


def _normalize(answer: dict) -> dict:
    out = dict(answer)
    for key in ("file", "evidence_file", "origin_file"):
        path = posixpath.normpath(str(out[key]).replace("\\", "/"))
        out[key] = path.removeprefix("./")
    return out


def _adopted_result(rule: LocateResult, answer: dict, tools: AppTools,
                    over_budget: bool) -> LocateResult:
    file, line = answer["file"], answer["line"]
    found = [Location.at(file, line)]
    lines = tools.file_lines(file) or []
    start, end = statement_span(lines, line)
    if (start, end) != (line, line):
        found.append(Location(file, start, end, role="statement_span"))
    if rule.primary is not None:
        found.append(replace(rule.primary, role="rule_answer"))
    crash = _crash_line(rule)
    if crash is not None:
        found.append(replace(crash, role="crash_line"))
    seen: set[tuple[str, int, int]] = set()
    locations = []
    for loc in found:
        key = (loc.file, loc.start_line, loc.end_line)
        if key not in seen:
            seen.add(key)
            locations.append(loc)
    evidence = (*rule.evidence,
                f"llm quote {file}:{line}: {answer['quoted_line'].strip()}",
                (f"llm evidence {answer['evidence_file']}:{answer['evidence_line']}: "
                 f"{answer['evidence_quote'].strip()}"),
                f"llm reasoning: {answer['reasoning'][:300]}")
    assumptions = [*rule.assumptions,
                   "llm answer adopted; quote checks prevent fabrication, not wrong answers"]
    if over_budget:
        assumptions.append("over token budget")
    return replace(rule, method=METHOD, root_cause_locations=tuple(locations),
                   evidence=evidence, assumptions=tuple(assumptions), status="CANDIDATE")


def _failed_session(error: str) -> SessionResult:
    return SessionResult(answer=None, error=error, over_budget=False, budget_warned=False,
                         turns=0, tokens=0, cache_read_tokens=0, seconds=0.0, calls=(),
                         transcript=(), meta={})


def _slug(case_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)


class LlmLocator:
    """Callable with the same signature as the other locators."""

    def __init__(self, backend: Backend, config: LlmConfig | None = None,
                 record_dir: Path | None = None) -> None:
        self.backend = backend
        self.config = config or LlmConfig()
        self.record_dir = record_dir
        self.prompt, self.prompt_sha256 = load_prompt()
        self.stats: list[dict] = []

    def __call__(self, case_id: str, ctx: CrashContext, index: SourceIndex,
                 source_hash: str = "") -> LocateResult:
        rule = locate_with_rules(case_id, ctx, index, source_hash)
        log_text = (index.app_root.parent / "crash.log").read_text(encoding="utf-8")
        cfg = self.config
        tools = AppTools(index.app_root, cfg.read_limit, cfg.search_limit)
        user_prompt = build_user_prompt(log_text, rule, tools, cfg)
        request = SessionRequest(case_id, index.app_root, self.prompt, user_prompt, cfg)
        try:
            session = self.backend.run(request)
        except Exception as exc:  # an LLM failure must leave the rule answer, not an ERROR case
            logger.exception("%s: backend %s failed", case_id, self.backend.name)
            session = _failed_session(repr(exc))

        answer = _normalize(session.answer) if session.answer is not None else None
        checks: dict[str, bool] = {}
        if answer is None:
            adopted, reason = False, f"no answer: {session.error}"
        else:
            checks = check_answer(answer, tools)
            adopted, reason = adopt(answer, checks, rule, session.over_budget)
        if adopted and answer is not None:
            result = _adopted_result(rule, answer, tools, session.over_budget)
        else:
            result = replace(rule, method=METHOD,
                             assumptions=(*rule.assumptions, f"llm fallback: {reason}"))
        logger.info("%s: %s (%d tokens)", case_id, reason, session.tokens)

        self.stats.append({
            "case_id": case_id, "adopted": adopted, "reason": reason,
            "tokens": session.tokens, "cache_read_tokens": session.cache_read_tokens,
            "turns": session.turns, "seconds": session.seconds,
            "over_budget": session.over_budget, "error": session.error,
        })
        self._record(case_id, user_prompt, answer, checks, adopted, reason, session)
        return result

    def _record(self, case_id: str, user_prompt: str, answer: dict | None,
                checks: dict[str, bool], adopted: bool, reason: str,
                session: SessionResult) -> None:
        if self.record_dir is None:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(session).items() if k != "transcript"}
        record = {
            "case_id": case_id, "prompt_version": PROMPT_VERSION,
            "prompt_sha256": self.prompt_sha256, "backend": self.backend.name,
            "config": asdict(self.config), "user_prompt": user_prompt, "answer": answer,
            "checks": checks, "adopted": adopted, "reason": reason, "session": data,
            "transcript": list(session.transcript),
        }
        path = self.record_dir / f"{_slug(case_id)}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
