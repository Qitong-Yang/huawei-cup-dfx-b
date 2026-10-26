"""Project-relative defaults; explicit CLI paths retain normal cwd-relative semantics."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data-master" / "data_B"
DEFAULT_CASES = PROJECT_ROOT / "resources" / "cases.json"
DEFAULT_GROUND_TRUTH = PROJECT_ROOT / "resources" / "ground_truth.json"
DEFAULT_SANDBOX_ROOT = PROJECT_ROOT / "eval" / "sandbox"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "eval" / "runs"
