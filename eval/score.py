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


def _span(app_dir: Path, file: str, line: int) -> tuple[int, int] | None:
    path = app_dir / file
    if not path.is_file():
        return None
    span = enclosing_function(path.read_text(encoding="utf-8", errors="replace").splitlines(), line)
    return (span.start_line, span.end_line) if span else None


def score_case(pred: LocateResult, gt: dict, app_dir: Path) -> CaseScore:
    accepted = [(a["file"], a["line"]) for a in gt["accepted_lines"]]
    ranges = [(r["file"], r["start_line"], r["end_line"]) for r in gt["root_cause_locations"]]
    primary = pred.primary
    if primary is None:
        return CaseScore(gt["case_id"], None, dict.fromkeys(LEVELS, False), False)

    f, ln = primary.file, primary.start_line
    exact = (f, ln) in accepted
    in_range = exact or any(f == rf and s <= ln <= e for rf, s, e in ranges)
    function = in_range
    if not function:
        pred_span = _span(app_dir, f, ln)
        for af, al in accepted:
            if af != f:
                continue
            acc_span = _span(app_dir, af, al)
            if acc_span is None:
                function = abs(al - ln) <= FUNCTION_FALLBACK_LINES
            else:
                function = pred_span == acc_span
            if function:
                break
    files = {a for a, _ in accepted} | {rf for rf, _, _ in ranges}
    hits = {"exact": exact, "range": in_range, "function": function, "file": f in files}
    topk = any((loc.file, loc.start_line) in accepted for loc in pred.root_cause_locations[:TOP_K])
    return CaseScore(gt["case_id"], f"{f}:{ln}", hits, topk)


def score_run(predictions: list[LocateResult], ground_truth: dict, app_dirs: dict[str, Path]) -> dict:
    """Return a summary dict with per-case details and rates (with denominators)."""
    gt_by_id = {g["case_id"]: g for g in ground_truth["entries"]}
    pred_by_id = {p.case_id: p for p in predictions}
    scorable = [g for g in gt_by_id.values() if "excluded" not in g]
    excluded = {g["case_id"]: g["excluded"] for g in gt_by_id.values() if "excluded" in g}

    cases: list[CaseScore] = []
    missing: list[str] = []
    for gt in scorable:
        pred = pred_by_id.get(gt["case_id"])
        if pred is None:
            missing.append(gt["case_id"])
            pred = LocateResult(gt["case_id"], "none", None, ())
        cases.append(score_case(pred, gt, app_dirs[gt["case_id"]]))

    n = len(cases)
    rates = {lvl: {"hits": sum(c.hits[lvl] for c in cases), "total": n} for lvl in LEVELS}
    rates[f"top{TOP_K}_exact"] = {"hits": sum(c.topk_exact for c in cases), "total": n}
    return {
        "rates": rates,
        "excluded": excluded,
        "missing_predictions": missing,
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
    lines += ["", "| 案例 | 预测 | exact | range | function | file |", "|---|---|---|---|---|---|"]
    for c in summary["cases"]:
        mark = ["✓" if c["hits"][lvl] else "✗" for lvl in LEVELS]
        lines.append(f"| {c['case_id']} | `{c['predicted']}` | " + " | ".join(mark) + " |")
    return "\n".join(lines) + "\n"


def load_ground_truth(path: Path = Path("resources/ground_truth.json")) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
