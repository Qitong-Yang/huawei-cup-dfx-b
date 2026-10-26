"""Score localization predictions against resources/ground_truth.json.

Hit levels for the primary answer (first root_cause_location):
  exact     line is in accepted_lines
  range     line falls inside any ground-truth root_cause_locations range (incl. accepted lines)
  function  same enclosing function/block as an accepted line; for accepted lines outside any
            function (data, fields) within FUNCTION_FALLBACK_LINES lines
  file      same file as any accepted line or ground-truth range
Top-k exact: any of the first K predicted locations is an accepted line.
Excluded ground-truth cases are reported separately; every rate states its denominator.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from dfx_repair.locate import LocateResult
from dfx_repair.retrieve import enclosing_function

from .paths import DEFAULT_GROUND_TRUTH

logger = logging.getLogger(__name__)

TOP_K = 3
FUNCTION_FALLBACK_LINES = 3
LEVELS = ("exact", "range", "function", "file")


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    predicted: str | None
    hits: dict[str, bool]
    topk_exact: bool
    invalid_reason: str | None = None


def _valid_location(app_dir: Path, location) -> bool:
    """Reject nonexistent, escaping or malformed ranges before granting any metric hit."""
    if not isinstance(location.file, str) or not location.file or "\\" in location.file:
        return False
    rel = Path(location.file)
    if rel.is_absolute() or ":" in location.file or ".." in rel.parts:
        return False
    if (not isinstance(location.start_line, int) or isinstance(location.start_line, bool)
            or not isinstance(location.end_line, int) or isinstance(location.end_line, bool)
            or not 1 <= location.start_line <= location.end_line):
        return False
    try:
        root = app_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        return location.end_line <= len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except (OSError, RuntimeError, ValueError):
        return False


def _span(app_dir: Path, file: str, line: int) -> tuple[int, int] | None:
    path = app_dir / file
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not 1 <= line <= len(lines):
        return None
    span = enclosing_function(lines, line)
    return (span.start_line, span.end_line) if span else None


def score_case(pred: LocateResult, gt: dict, app_dir: Path) -> CaseScore:
    accepted = [(a["file"], a["line"]) for a in gt["accepted_lines"]]
    ranges = [(r["file"], r["start_line"], r["end_line"]) for r in gt["root_cause_locations"]]
    primary = pred.primary
    if primary is None:
        return CaseScore(gt["case_id"], None, dict.fromkeys(LEVELS, False), False)
    if pred.case_id != gt["case_id"] or getattr(pred, "status", "CANDIDATE") != "CANDIDATE":
        return CaseScore(gt["case_id"], None, dict.fromkeys(LEVELS, False), False,
                         "case ID or status is inconsistent with an executable candidate")

    valid_candidates = [loc for loc in pred.root_cause_locations[:TOP_K]
                        if _valid_location(app_dir, loc)]
    topk = any((loc.file, loc.start_line) in accepted for loc in valid_candidates)
    if not _valid_location(app_dir, primary):
        return CaseScore(gt["case_id"], None, dict.fromkeys(LEVELS, False), topk,
                         "primary location is outside the source snapshot or has an invalid range")

    f, ln = primary.file, primary.start_line
    exact = (f, ln) in accepted
    in_range = exact or any(f == rf and s <= ln <= e for rf, s, e in ranges)
    # A related/crash-site range may live in a completely different function/file.
    # Function hits are defined independently against accepted root-cause lines.
    function = exact
    if not function:
        pred_span = _span(app_dir, f, ln)
        for af, al in accepted:
            if af != f:
                continue
            acc_span = _span(app_dir, af, al)
            if acc_span is None:
                function = pred_span is None and abs(al - ln) <= FUNCTION_FALLBACK_LINES
            else:
                function = pred_span == acc_span
            if function:
                break
    files = {a for a, _ in accepted} | {rf for rf, _, _ in ranges}
    hits = {"exact": exact, "range": in_range, "function": function, "file": f in files}
    return CaseScore(gt["case_id"], f"{f}:{ln}", hits, topk)


def score_run(predictions: list[LocateResult], ground_truth: dict, app_dirs: dict[str, Path]) -> dict:
    """Return a summary dict with per-case details and rates (with denominators)."""
    gt_by_id = {g["case_id"]: g for g in ground_truth["entries"]}
    pred_by_id = {p.case_id: p for p in predictions}
    if len(gt_by_id) != len(ground_truth["entries"]):
        raise ValueError("duplicate case IDs in ground truth")
    if len(pred_by_id) != len(predictions):
        raise ValueError("duplicate case IDs in predictions")
    scorable = [g for g in gt_by_id.values() if "excluded" not in g]
    excluded = {g["case_id"]: g["excluded"] for g in gt_by_id.values() if "excluded" in g}

    cases: list[CaseScore] = []
    missing: list[str] = []
    for gt in scorable:
        pred = pred_by_id.get(gt["case_id"])
        if pred is None:
            missing.append(gt["case_id"])
            pred = LocateResult(gt["case_id"], "none", None, ())
        app_dir = app_dirs.get(gt["case_id"])
        if app_dir is None and pred.primary is not None:
            cases.append(CaseScore(gt["case_id"], None, dict.fromkeys(LEVELS, False), False,
                                   "source snapshot unavailable"))
        else:
            cases.append(score_case(pred, gt, app_dir or Path(".")))

    n = len(cases)
    rates = {lvl: {"hits": sum(c.hits[lvl] for c in cases), "total": n} for lvl in LEVELS}
    rates[f"top{TOP_K}_exact"] = {"hits": sum(c.topk_exact for c in cases), "total": n}
    return {
        "rates": rates,
        "excluded": excluded,
        "missing_predictions": missing,
        "invalid_predictions": {c.case_id: c.invalid_reason for c in cases if c.invalid_reason},
        "unknown_predictions": sorted(set(pred_by_id) - set(gt_by_id)),
        "cases": [c.__dict__ for c in cases],
    }


def render_markdown(summary: dict, title: str) -> str:
    lines = [f"# {title}", "", "| 指标 | 命中 | 分母 | 比例 |", "|---|---|---|---|"]
    for name, r in summary["rates"].items():
        pct = 100.0 * r["hits"] / r["total"] if r["total"] else 0.0
        lines.append(f"| {name} | {r['hits']} | {r['total']} | {pct:.1f}% |")
    lines += ["", f"排除案例（不计入分母）：{len(summary['excluded'])}"]
    lines += [f"- `{cid}`：{why}" for cid, why in summary["excluded"].items()]
    if summary["missing_predictions"]:
        lines += ["", "缺少预测（按未命中计）：" + ", ".join(summary["missing_predictions"])]
    if summary.get("coverage"):
        lines += ["", "执行覆盖（不从主指标分母中删除阻塞／错误案例）："]
        lines += [f"- {name}: {value}" for name, value in summary["coverage"].items()]
    if summary.get("invalid_predictions"):
        lines += ["", "无效预测："]
        lines += [f"- `{cid}`: {reason}" for cid, reason in summary["invalid_predictions"].items()]
    lines += ["", "| 案例 | 预测 | exact | range | function | file |", "|---|---|---|---|---|---|"]
    for c in summary["cases"]:
        mark = ["✓" if c["hits"][lvl] else "✗" for lvl in LEVELS]
        lines.append(f"| {c['case_id']} | `{c['predicted']}` | " + " | ".join(mark) + " |")
    if summary.get("llm"):
        lines += _llm_section(summary)
    return "\n".join(lines) + "\n"


def _llm_section(summary: dict) -> list[str]:
    llm = summary["llm"]
    totals = (f"tokens {summary['tokens']}，缓存读占比 {100 * llm['cache_read_ratio']:.1f}%，"
              f"平均耗时 {llm['mean_seconds']} s/案例，prompt {llm['prompt_version']} "
              f"({llm['prompt_sha256'][:12]})")
    lines = ["", "## LLM 用量", "", totals, "",
             "| 案例 | 采纳 | 原因 | 轮数 | tokens | 缓存读 | 耗时 s |",
             "|---|---|---|---|---|---|---|"]
    for s in llm["cases"]:
        reason = str(s["reason"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {s['case_id']} | {'✓' if s['adopted'] else '✗'} | {reason} | "
                     f"{s['turns']} | {s['tokens']} | {s['cache_read_tokens']} | {s['seconds']} |")
    return lines


def load_ground_truth(path: Path = DEFAULT_GROUND_TRUTH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
