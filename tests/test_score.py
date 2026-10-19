"""Unit tests for eval.score and eval.sandbox helpers."""

from pathlib import Path

from eval.sandbox import copy_allowlisted, scan_leaks
from eval.score import score_case, score_run

from dfx_repair.locate import LocateResult, Location

APP_SOURCE = """export class VM {
  public sort(index1: number, index2: number) {
    if (index1 >= list.length) {
      return;
    }
    let a = list[index1].sortNum;
    let b = list[index2].sortNum;
  }

  public other() {
    return 1;
  }
}
"""

GT = {
    "case_id": "C1",
    "accepted_lines": [{"file": "a/VM.ets", "line": 3}],
    "root_cause_locations": [{"file": "a/VM.ets", "start_line": 3, "end_line": 5, "role": "missing_check"}],
}


def _app(tmp_path: Path) -> Path:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "VM.ets").write_text(APP_SOURCE, encoding="utf-8")
    return tmp_path


def _pred(*lines: int, file: str = "a/VM.ets") -> LocateResult:
    return LocateResult("C1", "test", None, tuple(Location.at(file, n) for n in lines))


def test_hit_levels(tmp_path: Path):
    app = _app(tmp_path)
    exact = score_case(_pred(3), GT, app)
    assert exact.hits == {"exact": True, "range": True, "function": True, "file": True}

    crash_line = score_case(_pred(7), GT, app)  # same function, outside the range
    assert crash_line.hits == {"exact": False, "range": False, "function": True, "file": True}

    other_fn = score_case(_pred(11), GT, app)
    assert other_fn.hits == {"exact": False, "range": False, "function": False, "file": True}

    wrong_file = score_case(_pred(3, file="b/X.ets"), GT, app)
    assert not any(wrong_file.hits.values())


def test_topk_and_denominators(tmp_path: Path):
    app = _app(tmp_path)
    gt = {"entries": [GT, {"case_id": "C2", "excluded": "no log"}]}
    summary = score_run([_pred(7, 3)], gt, {"C1": app})
    assert summary["rates"]["exact"] == {"hits": 0, "total": 1}
    assert summary["rates"]["top3_exact"] == {"hits": 1, "total": 1}
    assert summary["excluded"] == {"C2": "no log"}


def test_missing_prediction_counts_as_miss(tmp_path: Path):
    summary = score_run([], {"entries": [GT]}, {"C1": _app(tmp_path)})
    assert summary["missing_predictions"] == ["C1"]
    assert summary["rates"]["file"] == {"hits": 0, "total": 1}


def test_allowlist_copy_excludes_tests_and_answers(tmp_path: Path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for rel, text in {
        "entry/src/main/ets/A.ets": "let a = 1;",
        "testcases/TC_JSError_001.py": "fault_line = 78",
        "TC_JSError_001-App/faultlogger/x.log": "log",
        "docs/report.md": "故障位置",
        "oh_modules/dep/B.ets": "dep",
        "AppScope/app.json5": "{}",
    }.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    digest, count = copy_allowlisted(src, dst)
    copied = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert copied == ["AppScope/app.json5", "entry/src/main/ets/A.ets"]
    assert count == 2 and len(digest) == 16
    assert scan_leaks(dst) == []
