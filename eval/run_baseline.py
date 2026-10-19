"""Run the stack-top baseline on every scorable case and score it.

Usage:
    uv run python -m eval.run_baseline [--sandbox-root eval/sandbox] [--rebuild]

Writes eval/runs/<run_id>/{predictions.json, score.json, report.md}.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from dfx_repair.ingest import parse_crash_log
from dfx_repair.locate import locate_stack_top
from dfx_repair.retrieve import SourceIndex

from .sandbox import build_sandbox
from .score import load_ground_truth, render_markdown, score_run

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=Path("data-master/data_B"))
    parser.add_argument("--sandbox-root", type=Path, default=Path("eval/sandbox"))
    parser.add_argument("--runs-root", type=Path, default=Path("eval/runs"))
    parser.add_argument("--rebuild", action="store_true", help="rebuild sandboxes even if cached")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cases = {c["case_id"]: c for c in json.loads(Path("resources/cases.json").read_text())["cases"]}
    gt = load_ground_truth()
    run_id = datetime.now().strftime("stack_top_%Y%m%d_%H%M%S")
    run_dir = args.runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    predictions, app_dirs, timings = [], {}, {}
    for entry in gt["entries"]:
        if "excluded" in entry:
            continue
        cid = entry["case_id"]
        box = build_sandbox(cases[cid], args.data_root, args.sandbox_root, rebuild=args.rebuild)
        app_dirs[cid] = box.app_dir
        start = time.perf_counter()
        ctx = parse_crash_log(box.log_path.read_text(encoding="utf-8"))
        result = locate_stack_top(cid, ctx, SourceIndex(box.app_dir), box.source_hash)
        timings[cid] = round(time.perf_counter() - start, 3)
        predictions.append(result)
        logger.info("%-44s -> %s", cid, result.primary and f"{result.primary.file}:{result.primary.start_line}")

    summary = score_run(predictions, gt, app_dirs)
    summary["run_id"] = run_id
    summary["locate_seconds"] = timings
    summary["llm_calls"] = 0
    summary["tokens"] = 0
    (run_dir / "predictions.json").write_text(
        json.dumps([p.to_dict() for p in predictions], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "score.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(render_markdown(summary, f"Baseline: {run_id}"), encoding="utf-8")
    for name, r in summary["rates"].items():
        logger.info("%-12s %2d/%d", name, r["hits"], r["total"])
    logger.info("report: %s", run_dir / "report.md")


if __name__ == "__main__":
    main()
