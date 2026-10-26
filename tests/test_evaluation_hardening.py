"""Regression tests for independent scoring and failure-isolated offline evaluation."""

import json
from types import SimpleNamespace

import pytest
from eval import build_ground_truth, run_baseline
from eval.paths import DEFAULT_CASES, DEFAULT_DATA_ROOT, PROJECT_ROOT
from eval.score import score_case, score_run

from dfx_repair.locate import LocateResult, Location


def _app(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "Root.ets").write_text("function root() {\n  return 1;\n}\n", encoding="utf-8")
    (app / "Crash.ets").write_text("function crash() {\n  throw Error();\n}\n", encoding="utf-8")
    return app


def _gt(cid="C"):
    return {"case_id": cid, "crash": {"file": "Crash.ets", "line": 2},
            "accepted_lines": [{"file": "Root.ets", "line": 2}],
            "root_cause_locations": [{"file": "Crash.ets", "start_line": 2, "end_line": 2,
                                      "role": "crash_site"}]}


def _pred(*locations, cid="C"):
    return LocateResult(cid, "test", None, locations)


def test_related_range_and_root_function_are_independent(tmp_path):
    app = _app(tmp_path)
    result = score_case(_pred(Location.at("Crash.ets", 2)), _gt(), app)
    assert result.hits == {"exact": False, "range": True, "function": False, "file": True}


@pytest.mark.parametrize("location", [
    Location("Root.ets", 0, 0), Location("Root.ets", -1, 2),
    Location("Root.ets", 2, 1), Location("Root.ets", 2, 200),
    Location.at("Missing.ets", 2), Location.at("../Root.ets", 2),
    Location.at("C:/outside/Root.ets", 2),
])
def test_invalid_primary_cannot_grant_a_hit(tmp_path, location):
    result = score_case(_pred(location), _gt(), _app(tmp_path))
    assert not any(result.hits.values())
    assert result.invalid_reason


def test_nonexistent_file_even_in_bad_label_is_not_credited(tmp_path):
    gt = _gt()
    gt["accepted_lines"] = [{"file": "Absent.ets", "line": 2}]
    result = score_case(_pred(Location.at("Absent.ets", 2)), gt, _app(tmp_path))
    assert not result.hits["exact"] and not result.topk_exact


def test_valid_later_candidate_still_counts_only_as_topk(tmp_path):
    result = score_case(_pred(Location("Root.ets", 2, 100), Location.at("Root.ets", 2)),
                        _gt(), _app(tmp_path))
    assert not result.hits["exact"] and result.topk_exact


def test_missing_snapshot_and_prediction_keep_denominator(tmp_path):
    summary = score_run([_pred(Location.at("Root.ets", 2))],
                        {"entries": [_gt(), _gt("missing")]}, {})
    assert summary["rates"]["exact"] == {"hits": 0, "total": 2}
    assert summary["missing_predictions"] == ["missing"]
    assert "C" in summary["invalid_predictions"]


def test_duplicate_predictions_and_labels_are_rejected(tmp_path):
    app = _app(tmp_path)
    p = _pred(Location.at("Root.ets", 2))
    with pytest.raises(ValueError, match="duplicate"):
        score_run([p, p], {"entries": [_gt()]}, {"C": app})
    with pytest.raises(ValueError, match="duplicate"):
        score_run([p], {"entries": [_gt(), _gt()]}, {"C": app})


@pytest.mark.parametrize("status", ["BLOCKED", "ERROR", "UNRESOLVED", "UNKNOWN"])
def test_non_candidate_status_cannot_carry_scoring_locations(tmp_path, status):
    pred = LocateResult("C", "test", None, (Location.at("Root.ets", 2),), status=status)
    result = score_case(pred, _gt(), _app(tmp_path))
    assert not any(result.hits.values()) and not result.topk_exact and result.invalid_reason


def test_ground_truth_check_reports_invalid_reference_without_crash(tmp_path, monkeypatch):
    app = _app(tmp_path)
    gt = _gt()
    gt["accepted_lines"] += [{"file": "Absent.ets", "line": 500},
                             {"file": "Root.ets", "line": 500}]
    gt["root_cause_locations"][0]["end_line"] = 100
    monkeypatch.setattr(build_ground_truth, "GT", [gt])
    problems = build_ground_truth.check({"C": {"app_dir": app.name}}, tmp_path)
    assert any("missing file" in problem for problem in problems)
    assert sum("out of range" in problem for problem in problems) == 2


def test_defaults_do_not_depend_on_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_CASES.is_absolute() and DEFAULT_CASES.parent == PROJECT_ROOT / "resources"
    assert DEFAULT_DATA_ROOT == PROJECT_ROOT / "data-master" / "data_B"


def test_case_failures_are_isolated_and_all_counted(tmp_path, monkeypatch):
    app = _app(tmp_path)
    log = tmp_path / "crash.log"
    log.write_text("Error name:TypeError\nStacktrace:\n at root (Root.ets:2:3)\n", encoding="utf-8")
    cases = [{"case_id": name} for name in ["blocked", "error", "unresolved", "ok"]]
    cases_path, gt_path = tmp_path / "cases.json", tmp_path / "gt.json"
    cases_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    gt_path.write_text(json.dumps({"entries": [_gt(c["case_id"]) for c in cases]}), encoding="utf-8")

    def sandbox(case, *args, **kwargs):
        if case["case_id"] == "blocked":
            raise ValueError("input contaminated or missing")
        return SimpleNamespace(app_dir=app, log_path=log, source_hash="snapshot", log_hash="log",
                               policy_hash="policy")

    def locator(cid, *args):
        if cid == "error":
            raise RuntimeError("bad locator input")
        return _pred(*(() if cid == "unresolved" else (Location.at("Root.ets", 2),)), cid=cid)

    monkeypatch.setattr(run_baseline, "build_sandbox", sandbox)
    monkeypatch.setitem(run_baseline.LOCATORS, "stack_top", locator)
    kwargs = {"data_root": tmp_path, "cases_path": cases_path, "ground_truth_path": gt_path,
              "sandbox_root": tmp_path / "boxes", "runs_root": tmp_path / "runs"}
    summary, run_dir = run_baseline.run_evaluation(**kwargs)
    assert summary["rates"]["exact"] == {"hits": 1, "total": 4}
    assert summary["coverage"] == {"scorable_total": 4, "executed": 2, "candidate": 1,
                                    "unresolved": 1, "blocked": 1, "error": 1}
    assert [row["status"] for row in summary["case_outcomes"]] == [
        "BLOCKED", "ERROR", "UNRESOLVED", "CANDIDATE"]
    assert (run_dir / "report.md").is_file() and (run_dir / "case_outcomes.json").is_file()
    assert summary["configuration"]["implementation_sha256"]
    assert summary["configuration"]["ground_truth_sha256"]
    assert summary["total_seconds"] >= sum(summary["locate_seconds"].values())
    _, second = run_baseline.run_evaluation(**kwargs)
    assert second != run_dir


def test_inventory_missing_case_still_produces_complete_report(tmp_path):
    cases_path, gt_path = tmp_path / "cases.json", tmp_path / "gt.json"
    cases_path.write_text('{"cases": []}', encoding="utf-8")
    gt_path.write_text(json.dumps({"entries": [_gt("absent")]}), encoding="utf-8")
    summary, run_dir = run_baseline.run_evaluation(
        data_root=tmp_path / "missing", cases_path=cases_path, ground_truth_path=gt_path,
        sandbox_root=tmp_path / "boxes", runs_root=tmp_path / "runs",
    )
    assert summary["coverage"]["blocked"] == 1
    assert summary["rates"]["file"] == {"hits": 0, "total": 1}
    predictions = json.loads((run_dir / "predictions.json").read_text(encoding="utf-8"))
    assert predictions[0]["status"] == "BLOCKED"
    assert not predictions[0]["root_cause_locations"]


def test_source_read_failure_does_not_grant_file_or_exact_hit(tmp_path, monkeypatch):
    app = _app(tmp_path)
    original = type(app).read_text

    def denied(path, *args, **kwargs):
        if path.name == "Root.ets":
            raise PermissionError("not readable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(app), "read_text", denied)
    result = score_case(_pred(Location.at("Root.ets", 2)), _gt(), app)
    assert not any(result.hits.values()) and not result.topk_exact


def test_configuration_does_not_change_with_cwd_or_regenerable_outputs(tmp_path, monkeypatch):
    # Hash function reads project implementation only; a run artifact cannot alter it.
    before = run_baseline.implementation_hash()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output.py").write_text("not project code", encoding="utf-8")
    assert run_baseline.implementation_hash() == before


def test_managed_snapshot_records_and_passes_complete_source_digest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    app = data / "app"
    app.mkdir(parents=True)
    (app / "Root.ets").write_text("function root() {\n  return 1;\n}\n", encoding="utf-8")
    (data / "crash.log").write_text(
        "Error name:TypeError\nStacktrace:\n at root (Root.ets:2:3)\n", encoding="utf-8")
    case = {"case_id": "C", "app_dir": "app", "primary_log": 0,
            "logs": [{"path": "crash.log"}]}
    cases_path, gt_path = tmp_path / "cases.json", tmp_path / "gt.json"
    cases_path.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    gt_path.write_text(json.dumps({"entries": [_gt()]}), encoding="utf-8")
    passed_hashes = []

    def locator(cid, ctx, index, source_hash):
        passed_hashes.append(source_hash)
        return LocateResult(cid, "test", None, (Location.at("Root.ets", 2),),
                            source_hash=source_hash)

    monkeypatch.setitem(run_baseline.LOCATORS, "stack_top", locator)
    summary, run_dir = run_baseline.run_evaluation(
        data_root=data, cases_path=cases_path, ground_truth_path=gt_path,
        sandbox_root=tmp_path / "boxes", runs_root=tmp_path / "runs")
    row = summary["case_outcomes"][0]
    assert row["status"] == "CANDIDATE"
    assert len(row["source_digest"]) == 64 and len(row["source_hash"]) == 16
    assert row["source_digest"].startswith(row["source_hash"])
    assert passed_hashes == [row["source_digest"]]
    pred = json.loads((run_dir / "predictions.json").read_text(encoding="utf-8"))[0]
    assert pred["source_hash"] == row["source_digest"]


def _llm_setup(tmp_path, monkeypatch, backend):
    app = _app(tmp_path)
    log = tmp_path / "crash.log"  # sandbox layout: crash.log next to app/
    log.write_text("Error name:TypeError\nStacktrace:\n at root (Root.ets:2:3)\n", encoding="utf-8")
    cases = [{"case_id": name} for name in ["one", "two"]]
    cases_path, gt_path = tmp_path / "cases.json", tmp_path / "gt.json"
    cases_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    gt_path.write_text(json.dumps({"entries": [_gt(c["case_id"]) for c in cases]}),
                       encoding="utf-8")
    monkeypatch.setattr(run_baseline, "build_sandbox", lambda *a, **k: SimpleNamespace(
        app_dir=app, log_path=log, source_hash="snapshot", log_hash="log", policy_hash="p"))
    monkeypatch.setattr(run_baseline, "make_llm_backend", lambda: backend)
    return {"data_root": tmp_path, "cases_path": cases_path, "ground_truth_path": gt_path,
            "sandbox_root": tmp_path / "boxes", "runs_root": tmp_path / "runs"}


ROOT_ANSWER = {"hypothesis": "crash_line", "file": "Root.ets", "line": 2,
               "quoted_line": "return 1;", "evidence_file": "Root.ets", "evidence_line": 1,
               "evidence_quote": "function root() {", "origin_file": "Root.ets",
               "origin_line": 2, "origin_quote": "return 1;", "reasoning": "r"}


def test_rules_llm_run_records_usage_and_prompt_hash(tmp_path, monkeypatch):
    from tests.llm_fakes import FAKE_TOKENS, FakeBackend

    kwargs = _llm_setup(tmp_path, monkeypatch, FakeBackend(answer=ROOT_ANSWER))
    summary, run_dir = run_baseline.run_evaluation(**kwargs, method="rules+llm")
    assert summary["tokens"] == 2 * FAKE_TOKENS and summary["llm_calls"] == 6
    assert summary["llm"]["cases"][0]["adopted"] is True
    assert summary["llm"]["cache_read_ratio"] == 0.5 and summary["llm"]["mean_seconds"] == 1.0
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    assert cfg["llm"]["prompt_sha256"].startswith("12e09676") and cfg["llm"]["backend"] == "fake"
    assert cfg["llm"]["model"] == "claude-opus-5-5" and cfg["llm"]["prompt_version"] == "v4"
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## LLM 用量" in report and "| one | ✓ | adopted |" in report
    assert sorted(p.name for p in (run_dir / "llm").iterdir()) == ["one.json", "two.json"]
    assert summary["rates"]["exact"] == {"hits": 2, "total": 2}


def test_rules_llm_backend_failure_is_not_an_error_case(tmp_path, monkeypatch):
    from tests.llm_fakes import FakeBackend

    kwargs = _llm_setup(tmp_path, monkeypatch, FakeBackend(error="timeout 900s"))
    summary, _ = run_baseline.run_evaluation(**kwargs, method="rules+llm")
    assert summary["coverage"]["error"] == 0 and summary["coverage"]["candidate"] == 2
    assert summary["llm"]["cases"][0]["reason"] == "no answer: timeout 900s"


def test_other_methods_have_no_llm_section(tmp_path, monkeypatch):
    kwargs = _llm_setup(tmp_path, monkeypatch, None)
    summary, run_dir = run_baseline.run_evaluation(**kwargs, method="rules")
    assert "llm" not in summary and summary["tokens"] == 0
    assert "LLM 用量" not in (run_dir / "report.md").read_text(encoding="utf-8")
    assert "llm" not in json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
