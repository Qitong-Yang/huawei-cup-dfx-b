"""Run an offline locator with per-case failure isolation and complete denominators.

All default paths are project-relative. Explicit CLI paths are relative to the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import time
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dfx_repair.ingest import parse_crash_log
from dfx_repair.llm import PROMPT_VERSION, Backend, LlmConfig
from dfx_repair.llm.claude_cli import ClaudeCliBackend
from dfx_repair.locate import LlmLocator, LocateResult, locate_stack_top, locate_with_rules
from dfx_repair.locate.llm import METHOD as LLM_METHOD
from dfx_repair.retrieve import SourceIndex

from .paths import (
    DEFAULT_CASES,
    DEFAULT_DATA_ROOT,
    DEFAULT_GROUND_TRUTH,
    DEFAULT_RUNS_ROOT,
    DEFAULT_SANDBOX_ROOT,
    PROJECT_ROOT,
)
from .sandbox import Sandbox, build_sandbox
from .score import load_ground_truth, render_markdown, score_run

logger = logging.getLogger(__name__)
LOCATORS = {"stack_top": locate_stack_top, "rules": locate_with_rules}
METHODS = sorted([*LOCATORS, LLM_METHOD])


def make_llm_backend() -> Backend:
    """The backend for ``rules+llm`` runs: local ``claude -p`` (subscription login)."""
    return ClaudeCliBackend()


def _llm_summary(locator: LlmLocator) -> dict:
    stats = locator.stats
    tokens = sum(s["tokens"] for s in stats)
    cache_read = sum(s["cache_read_tokens"] for s in stats)
    return {
        "llm_calls": sum(s["turns"] for s in stats), "tokens": tokens,
        "llm": {"cases": stats, "cache_read_ratio": round(cache_read / tokens, 4) if tokens else 0.0,
                "mean_seconds": round(sum(s["seconds"] for s in stats) / len(stats), 2)
                if stats else 0.0,
                "prompt_version": PROMPT_VERSION, "prompt_sha256": locator.prompt_sha256},
    }


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_digest(box) -> str:
    """Use the managed snapshot's complete identity, not its legacy short identifier."""
    if not isinstance(box, Sandbox):
        # Test doubles may omit the managed manifest; they must not fabricate a digest.
        return getattr(box, "source_digest", "")
    manifest = json.loads((box.root / "manifest.json").read_text(encoding="utf-8"))
    digest = manifest.get("source_digest", "") if isinstance(manifest, dict) else ""
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or digest[:16] != box.source_hash):
        raise ValueError("sandbox manifest has no valid complete source SHA256 identity")
    return digest


def implementation_hash() -> str:
    """Hash only implementation .py files, never environments, caches or run artifacts."""
    digest = hashlib.sha256()
    files = [*(PROJECT_ROOT / "src").rglob("*.py"), *(PROJECT_ROOT / "eval").glob("*.py")]
    for path in sorted(files):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def _unique_by_id(entries: list[dict], label: str) -> dict[str, dict]:
    result = {entry["case_id"]: entry for entry in entries}
    if len(result) != len(entries):
        raise ValueError(f"duplicate case IDs in {label}")
    return result


def run_evaluation(*, data_root: Path = DEFAULT_DATA_ROOT,
                   cases_path: Path = DEFAULT_CASES,
                   ground_truth_path: Path = DEFAULT_GROUND_TRUTH,
                   sandbox_root: Path = DEFAULT_SANDBOX_ROOT,
                   runs_root: Path = DEFAULT_RUNS_ROOT,
                   method: str = "stack_top", rebuild: bool = False) -> tuple[dict, Path]:
    """Keep unavailable and error cases in the primary scoring denominator.

    BLOCKED: inventory/source/log/sandbox input could not be prepared (including leaks).
    ERROR: parsing/index/locator execution raised an exception.
    UNRESOLVED: execution completed but no primary source location was justified.
    CANDIDATE: a location was proposed; this is not a confirmed root cause or repair.
    """
    if method not in METHODS:
        raise ValueError(f"unknown locator: {method}")
    started = datetime.now(timezone.utc)
    run_start = time.perf_counter()
    cases = _unique_by_id(json.loads(cases_path.read_text(encoding="utf-8"))["cases"], "inventory")
    gt = load_ground_truth(ground_truth_path)
    _unique_by_id(gt["entries"], "ground truth")
    run_id = f"{method}_{started:%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    config = {
        "method": method, "data_root": str(data_root.resolve()),
        "cases_path": str(cases_path.resolve()), "cases_sha256": _file_hash(cases_path),
        "ground_truth_path": str(ground_truth_path.resolve()),
        "ground_truth_sha256": _file_hash(ground_truth_path),
        "sandbox_root": str(sandbox_root.resolve()), "rebuild": rebuild,
        "implementation_sha256": implementation_hash(), "python": platform.python_version(),
        "started_at_utc": started.isoformat(),
    }
    llm_locator = None
    if method == LLM_METHOD:
        llm_locator = LlmLocator(make_llm_backend(), LlmConfig(), run_dir / "llm")
        config["llm"] = {"backend": llm_locator.backend.name, "prompt_version": PROMPT_VERSION,
                         "prompt_sha256": llm_locator.prompt_sha256,
                         **asdict(llm_locator.config)}
    locator = llm_locator or LOCATORS[method]
    _write_json(run_dir / "config.json", config)
    predictions, app_dirs, outcomes, timings = [], {}, [], {}
    for entry in gt["entries"]:
        if "excluded" in entry:
            continue
        cid = entry["case_id"]
        case_start = time.perf_counter()
        row = {"case_id": cid, "status": "BLOCKED", "reason": "", "stage": "sandbox"}
        box = None
        prepare_start = time.perf_counter()
        try:
            if cid not in cases:
                raise ValueError("case missing from inventory")
            box = build_sandbox(cases[cid], data_root, sandbox_root, rebuild=rebuild)
            row["sandbox_seconds"] = round(time.perf_counter() - prepare_start, 6)
            row["source_hash"] = box.source_hash
            row["source_digest"] = _source_digest(box)
            row["log_hash"] = getattr(box, "log_hash", "") or _file_hash(box.log_path)
            row["policy_hash"] = getattr(box, "policy_hash", "")
            app_dirs[cid] = box.app_dir
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            row.update(reason=str(exc), exception_type=type(exc).__name__)
            logger.warning("%s BLOCKED: %s", cid, exc)
        except Exception as exc:  # Unexpected implementation errors are not input blockage.
            row.update(status="ERROR", reason=str(exc), exception_type=type(exc).__name__)
            logger.exception("%s ERROR preparing sandbox", cid)
        else:
            row["stage"] = "locate"
            locate_start = time.perf_counter()
            try:
                ctx = parse_crash_log(box.log_path.read_text(encoding="utf-8"))
                result = locator(cid, ctx, SourceIndex(box.app_dir),
                                 row["source_digest"] or box.source_hash)
                predictions.append(result)
                row["status"] = "CANDIDATE" if result.primary is not None else "UNRESOLVED"
                row["reason"] = "; ".join(result.assumptions)
                logger.info("%-44s -> %s", cid, result.primary and
                            f"{result.primary.file}:{result.primary.start_line}")
            except Exception as exc:
                row.update(status="ERROR", reason=str(exc), exception_type=type(exc).__name__)
                logger.exception("%s ERROR during localization", cid)
            finally:
                timings[cid] = round(time.perf_counter() - locate_start, 6)
                row["locate_seconds"] = timings[cid]
        if row["status"] in {"BLOCKED", "ERROR"}:
            predictions.append(LocateResult(cid, method, None, (), assumptions=(row["reason"],),
                                            source_hash=row.get("source_digest") or
                                            (box.source_hash if box else ""),
                                            status=row["status"]))
        row.setdefault("sandbox_seconds", round(time.perf_counter() - prepare_start, 6))
        row["total_seconds"] = round(time.perf_counter() - case_start, 6)
        outcomes.append(row)
        # Checkpoint after each case, so partial evidence survives later process interruption.
        _write_json(run_dir / "case_outcomes.json", outcomes)
    summary = score_run(predictions, gt, app_dirs)
    counts = Counter(row["status"] for row in outcomes)
    summary.update({
        "run_id": run_id, "configuration": config, "case_outcomes": outcomes,
        "coverage": {"scorable_total": len(outcomes),
                     "executed": counts["CANDIDATE"] + counts["UNRESOLVED"],
                     "candidate": counts["CANDIDATE"], "unresolved": counts["UNRESOLVED"],
                     "blocked": counts["BLOCKED"], "error": counts["ERROR"]},
        "locate_seconds": timings, "total_seconds": round(time.perf_counter() - run_start, 6),
        "llm_calls": 0, "tokens": 0,
    })
    if llm_locator is not None:
        summary.update(_llm_summary(llm_locator))
    _write_json(run_dir / "predictions.json", [p.to_dict() for p in predictions])
    _write_json(run_dir / "score.json", summary)
    (run_dir / "report.md").write_text(render_markdown(summary, f"Localization: {run_id}"),
                                      encoding="utf-8")
    for name, rate in summary["rates"].items():
        logger.info("%-12s %2d/%d", name, rate["hits"], rate["total"])
    logger.info("report: %s", run_dir / "report.md")
    return summary, run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--sandbox-root", type=Path, default=DEFAULT_SANDBOX_ROOT)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--method", choices=METHODS, default="stack_top")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary, _ = run_evaluation(data_root=args.data_root, cases_path=args.cases,
                                ground_truth_path=args.ground_truth, sandbox_root=args.sandbox_root,
                                runs_root=args.runs_root, method=args.method, rebuild=args.rebuild)
    if summary["coverage"]["blocked"] or summary["coverage"]["error"]:
        raise SystemExit(1)  # Report has already been saved, but incomplete execution is nonzero.


if __name__ == "__main__":
    main()
