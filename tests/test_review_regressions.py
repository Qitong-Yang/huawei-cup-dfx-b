"""Regression coverage for defects found in the Phase 4 review.

These tests assert the required behaviour and must pass without xfail/skip markers.
"""

from pathlib import Path

from eval.sandbox import build_sandbox
from eval.score import score_case

from dfx_repair.ingest import parse_crash_log, strip_leaky_sections
from dfx_repair.locate import LocateResult, Location, locate_with_rules
from dfx_repair.locate.rules.base import read_lines
from dfx_repair.retrieve import SourceIndex, enclosing_function


def _context(file="a/VM.ets", line=8, col=20, error="RangeError",
             message="getIndex +elementSize > viewSize"):
    return parse_crash_log(
        f"Error name:{error}\nError message:{message}\nStacktrace:\n"
        f" at run ({file}:{line}:{col})\n"
    )


def _source(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_completed_unrelated_loop_is_not_root_cause(tmp_path):
    src = "class VM {\n  run(buf: ArrayBuffer) {\n    for (let i = 0; i <= 3; i++) {\n      console.log(i);\n    }\n    const view = new DataView(buf);\n    const bad = buf.byteLength;\n    return view.getFloat32(bad);\n  }\n}"
    _source(tmp_path, "a/VM.ets", src)
    result = locate_with_rules("C", _context(), SourceIndex(tmp_path))
    assert not any("R-BOUNDS-LOOP" in e for e in result.evidence)


def test_informational_length_branch_is_not_missing_guard(tmp_path):
    src = "class VM {\n  run(a: number, b: number) {\n    const items = this.items;\n    if (a >= items.length) {\n      console.log('source index');\n    }\n    const unused = 1;\n    return items[b].order;\n  }\n}"
    _source(tmp_path, "a/VM.ets", src)
    col = src.splitlines()[7].index("order") + 1
    result = locate_with_rules(
        "C", _context(line=8, col=col, error="TypeError",
                      message="Cannot read property order of undefined"), SourceIndex(tmp_path)
    )
    assert not any("R-BOUNDS-PARTIAL" in e for e in result.evidence)


def test_ambiguous_mapping_does_not_become_single_primary_answer(tmp_path):
    for rel in ["a/VM.ets", "b/VM.ets"]:
        _source(tmp_path, rel, "const x = 1;\n")
    result = locate_with_rules("C", _context(file="VM.ets", line=1), SourceIndex(tmp_path))
    assert result.primary is None


def test_file_read_refreshes_after_source_change(tmp_path):
    p = _source(tmp_path, "a/VM.ets", "const version = 1;\n")
    read_lines(str(tmp_path), "a/VM.ets")
    p.write_text("const version = 2;\n", encoding="utf-8")
    assert read_lines(str(tmp_path), "a/VM.ets") == ("const version = 2;",)


def test_sandbox_cache_invalidates_on_source_change(tmp_path):
    data = tmp_path / "data"
    source = _source(data, "app/a/VM.ets", "const version = 1;\n")
    _source(data, "crash.log", "Error name:TypeError\n")
    case = {"case_id": "C", "app_dir": "app", "primary_log": 0,
            "logs": [{"path": "crash.log"}]}
    first = build_sandbox(case, data, tmp_path / "boxes")
    source.write_text("const version = 2;\n", encoding="utf-8")
    second = build_sandbox(case, data, tmp_path / "boxes")
    assert second.source_hash != first.source_hash


def test_unindented_answer_section_is_removed():
    text = "Error name:TypeError\nRoot cause:\nSECRET_ROOT_LOCATION\nFix:\nSECRET_PATCH\n"
    cleaned = strip_leaky_sections(text)
    assert "SECRET_" not in cleaned


def test_related_crash_range_is_not_root_function_match(tmp_path):
    _source(tmp_path, "Root.ets", "function root() {\n  return 'bad';\n}\n")
    _source(tmp_path, "Crash.ets", "function crash() {\n  throw Error();\n}\n")
    gt = {"case_id": "C", "accepted_lines": [{"file": "Root.ets", "line": 2}],
          "root_cause_locations": [{"file": "Crash.ets", "start_line": 2,
                                    "end_line": 2, "role": "crash_site"}]}
    pred = LocateResult("C", "test", None, (Location.at("Crash.ets", 2),))
    assert score_case(pred, gt, tmp_path).hits["function"] is False


def test_string_literal_brace_does_not_end_enclosing_function():
    lines = ["function run() {", "  const text = '}';", "  return 1;", "}"]
    span = enclosing_function(lines, 3)
    assert span is not None and span.start_line == 1 and span.end_line == 4


def test_empty_source_returns_unresolved_instead_of_crashing(tmp_path):
    _source(tmp_path, "a/VM.ets", "")
    result = locate_with_rules("C", _context(line=1), SourceIndex(tmp_path))
    assert result.primary is None
