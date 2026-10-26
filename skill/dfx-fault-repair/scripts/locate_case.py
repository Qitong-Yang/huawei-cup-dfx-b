"""Locate the root cause of one ArkTS crash and print the LocateResult as JSON.

The case directory must hold ``app/`` (application source) and ``crash.log``,
the layout produced by ``eval.sandbox``. Run from the repository root:

    uv run python skill/dfx-fault-repair/scripts/locate_case.py \
        --case-dir eval/sandbox/TC_JSError_001_ComprehensiveTool1_B1 --method rules
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from dfx_repair.ingest import decode_log_bytes, parse_crash_log
from dfx_repair.llm import LlmConfig
from dfx_repair.llm.claude_cli import ClaudeCliBackend
from dfx_repair.locate import (
    LlmLocator,
    LocateResult,
    locate_stack_top,
    locate_with_rules,
)
from dfx_repair.retrieve import SourceIndex

METHODS = ("stack_top", "rules", "rules+llm")


def locate(case_dir: Path, method: str, record_dir: Path | None = None) -> LocateResult:
    """Run one locator on ``case_dir`` and return its result."""
    log_path, app_dir = case_dir / "crash.log", case_dir / "app"
    if not log_path.is_file() or not app_dir.is_dir():
        raise FileNotFoundError(f"{case_dir} must contain app/ and crash.log")
    ctx = parse_crash_log(decode_log_bytes(log_path.read_bytes()))
    index = SourceIndex(app_dir)
    case_id = case_dir.name
    if method == "stack_top":
        return locate_stack_top(case_id, ctx, index)
    if method == "rules":
        return locate_with_rules(case_id, ctx, index)
    return LlmLocator(ClaudeCliBackend(), LlmConfig(), record_dir)(case_id, ctx, index)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--method", choices=METHODS, default="rules+llm")
    parser.add_argument("--record-dir", type=Path, default=None,
                        help="save the LLM transcript here (rules+llm only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = locate(args.case_dir, args.method, args.record_dir)
    json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
