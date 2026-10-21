"""Regression tests for conservative source structure and localization decisions."""

from pathlib import Path

import pytest

from dfx_repair.ingest import parse_crash_log
from dfx_repair.locate import LocateResult, Location, locate_stack_top, locate_with_rules
from dfx_repair.locate.rules.align import align_crash_line
from dfx_repair.locate.rules.base import CrashSite, expected_construct, read_lines
from dfx_repair.locate.rules.bounds import loop_off_by_one, partial_bounds_check
from dfx_repair.retrieve import SourceIndex, enclosing_function
from dfx_repair.retrieve.lexical import mask_non_code


def _context(path="a/VM.ets", line=1, col=1, error="TypeError", message="Cannot read property order of undefined"):
    return parse_crash_log(f"Error name:{error}\nError message:{message}\nStacktrace:\n at fn ({path}:{line}:{col})\n")


def _write(root: Path, source: str, rel="a/VM.ets"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


@pytest.mark.parametrize("literal", [
    "const text = '}';", 'const url = "https://host/}/*";',
    "/* } \n { */ const x = 1;", "const pattern = /[}\\/]/g;",
    "if (ok) /}/.test(value);", "while (ok) /[}]/.test(value);",
    "const text = `} ${`${value} }`} {`;", "return /}/.test(value);",
])
def test_literals_do_not_change_function_boundary(literal):
    lines = ["function run() {", *literal.splitlines(), "  const later = 1;", "}"]
    target = len(lines) - 1
    span = enclosing_function(lines, target)
    assert span is not None
    assert (span.name, span.start_line, span.end_line) == ("run", 1, len(lines))


def test_mask_preserves_positions_and_division():
    source = "const url = 'https://x/}';\nconst n = value / count; // }\n"
    code = mask_non_code(source)
    assert len(code) == len(source)
    assert [i for i, c in enumerate(code) if c == "\n"] == [i for i, c in enumerate(source) if c == "\n"]
    assert "value / count" in code and "https" not in code


def test_read_lines_uses_current_content_and_rejects_escape(tmp_path):
    p = _write(tmp_path, "const version = 1;\n")
    assert read_lines(str(tmp_path), "a/VM.ets") == ("const version = 1;",)
    p.write_text("const version = 2;\n", encoding="utf-8")
    assert read_lines(str(tmp_path), "a/VM.ets") == ("const version = 2;",)
    outside = tmp_path.parent / (tmp_path.name + "-outside.ets")
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        read_lines(str(tmp_path), "../" + outside.name)


@pytest.mark.parametrize("locator", [locate_stack_top, locate_with_rules])
def test_ambiguity_is_unresolved_for_both_locators(tmp_path, locator):
    for rel in ["a/VM.ets", "b/VM.ets"]:
        _write(tmp_path, "const x = 1;\n", rel)
    result = locator("C", _context(path="VM.ets"), SourceIndex(tmp_path))
    assert result.primary is None and result.status == "UNRESOLVED"
    assert result.context_requests



@pytest.mark.parametrize("locator", [locate_stack_top, locate_with_rules])
def test_ambiguous_files_are_handed_downstream_without_scoring(tmp_path, locator):
    for rel in ["a/VM.ets", "b/VM.ets"]:
        _write(tmp_path, "const x = 1;\n", rel)
    result = locator("C", _context(path="VM.ets"), SourceIndex(tmp_path))
    assert result.root_cause_locations == ()
    assert {(c.file, c.start_line) for c in result.unresolved_candidates} == {("a/VM.ets", 1), ("b/VM.ets", 1)}

@pytest.mark.parametrize("source,line", [("", 1), ("const x = 1;\n", 0), ("const x = 1;\n", 999)])
@pytest.mark.parametrize("locator", [locate_stack_top, locate_with_rules])
def test_bad_coordinates_do_not_crash_or_get_clamped(tmp_path, source, line, locator):
    _write(tmp_path, source)
    result = locator("C", _context(line=line), SourceIndex(tmp_path))
    assert result.primary is None and result.status == "UNRESOLVED"


def test_alignment_with_two_candidates_is_not_guessed():
    lines = ("const a = Object.keys(first);", "// no code", "const b = Object.keys(second);")
    ctx = _context(line=2, message="Cannot convert a UNDEFINED value to a JSObject")
    site = align_crash_line(ctx, "a/VM.ets", 2, 5, lines)
    assert not site.valid and site.line == 2
    assert site.alternatives == (1, 3)


def test_unresolved_alignment_preserves_valid_logged_crash_position(tmp_path):
    _write(tmp_path, "const a = Object.keys(first);\n// no matching code\nconst b = Object.keys(second);\n")
    result = locate_with_rules("C", _context(line=2, message="Cannot convert a UNDEFINED value to a JSObject"),
                               SourceIndex(tmp_path))
    assert result.status == "UNRESOLVED" and result.primary is None
    assert result.crash_location is not None
    assert (result.crash_location.file, result.crash_location.start_line,
            result.crash_location.role) == ("a/VM.ets", 2, "crash_site")
    assert [(c.file, c.start_line) for c in result.unresolved_candidates] == [("a/VM.ets", 1), ("a/VM.ets", 3)]


def test_trailing_empty_line_keeps_its_slot_without_index_error():
    lines = ("const x = 1;", "")
    ctx = _context(line=2)
    site = align_crash_line(ctx, "a/VM.ets", 2, 1, lines)
    assert not site.valid and site.line == 2
    assert enclosing_function(list(lines), 2) is None
    direct_site = CrashSite("a/VM.ets", 2, 1, lines)
    assert partial_bounds_check(ctx, direct_site) == []
    assert loop_off_by_one(_context(line=2, error="RangeError",
                                   message="getIndex +elementSize > viewSize"), direct_site) == []


def test_result_serialization_preserves_unresolved_and_legacy_fields():
    result = LocateResult("C", "rules", None, (), status="UNRESOLVED", context_requests=("more source",),
                          unresolved_candidates=(Location.at("a/VM.ets", 3, role="crash_site_candidate"),))
    assert LocateResult.from_dict(result.to_dict()) == result
    old = result.to_dict()
    del old["status"], old["context_requests"], old["unresolved_candidates"]
    assert LocateResult.from_dict(old).status == "CANDIDATE"


def test_circular_json_error_selects_stringify_not_parse():
    construct = expected_construct(_context(message="Converting circular structure to JSON"))
    assert construct is not None
    assert construct.search("JSON.stringify(notes)")
    assert not construct.search("JSON.parse(input)")


def test_json_parse_error_is_specific_and_does_not_cover_all_json_errors():
    construct = expected_construct(_context(error="SyntaxError", message="Unexpected token x in JSON at position 0"))
    assert construct is not None and construct.search("JSON.parse(input)")
    assert not construct.search("JSON.stringify(notes)")
    assert expected_construct(_context(message="Cannot serialize a BigInt value to JSON")) is None


def _locate_source(tmp_path, source, error, message):
    lines = source.splitlines()
    target = next(i + 1 for i, text in enumerate(lines) if "CRASH" in text)
    col = lines[target - 1].find("order") + 1 if "order" in lines[target - 1] else 1
    _write(tmp_path, source)
    return locate_with_rules("C", _context(line=target, col=col, error=error, message=message), SourceIndex(tmp_path))


@pytest.mark.parametrize("reverse", [False, True])
def test_separate_target_guard_prevents_partial_bounds_accusation(tmp_path, reverse):
    source = """class VM {
  run(a: number, b: number) {
    const items = this.items;
    if (a >= items.length) { return; }
    if (b >= items.length) { return; }
    return items[b].order; // CRASH
  }
}"""
    if reverse:
        source = source.replace("if (a >= items.length) { return; }\n    if (b >= items.length)",
                                "if (b >= items.length) { return; }\n    if (a >= items.length)")
    result = _locate_source(tmp_path, source, "TypeError", "Cannot read property order of undefined")
    assert not any("R-BOUNDS-PARTIAL" in e for e in result.evidence)


@pytest.mark.parametrize("bound,offset", [("buffer.byteLength / 4 - 1", "i * 4"),
                                         ("buffer.byteLength / 4", "0")])
def test_safe_last_index_or_unrelated_offset_not_off_by_one(tmp_path, bound, offset):
    source = f"""class VM {{
  run(buffer: ArrayBuffer) {{
    const view = new DataView(buffer);
    const count = {bound};
    for (let i = 0; i <= count; i++) {{
      const off = {offset};
      return view.getFloat32(off); // CRASH
    }}
  }}
}}"""
    result = _locate_source(tmp_path, source, "RangeError", "getIndex +elementSize > viewSize")
    assert not any("R-BOUNDS-LOOP" in e for e in result.evidence)


def test_loop_supports_nested_local_offset_dependencies(tmp_path):
    source = """class VM {
  run(size: number) {
    const buffer = new ArrayBuffer(size);
    const view = new DataView(buffer);
    const total = size / this.bytesPerFrame;
    for (let frame = 0; frame <= total; frame += this.skipFrames) {
      const frameOffset = frame * this.bytesPerFrame;
      for (let channel = 0; channel < this.channels; channel++) {
        const offset = frameOffset + channel * this.bytesPerSample;
        const sample = view.getFloat32(offset, true); // CRASH
      }
    }
  }
}"""
    result = _locate_source(tmp_path, source, "RangeError", "getIndex +elementSize > viewSize")
    assert result.primary is not None and result.primary.start_line == 6
    assert result.status == "CANDIDATE" and result.assumptions and result.context_requests
