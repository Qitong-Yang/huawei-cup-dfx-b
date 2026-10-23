from pathlib import Path

import pytest
from tests.llm_fakes import FULL_SUBMIT

from dfx_repair.llm.tools import TOOL_SCHEMAS, AppTools, validate_submit


@pytest.fixture
def app(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "a").mkdir(parents=True)
    lines = [f"line {n}" for n in range(1, 301)]
    lines[6], lines[7], lines[9] = "let author = 'Author_2';", "use(author);", "needle"
    (root / "a" / "A.ets").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "README.md").write_text("Author_2 needle\n", encoding="utf-8")
    (root / "b").mkdir()
    (root / "b" / "B.ets").write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    (root / "h").mkdir()
    (root / "h" / "H.ts").write_text("hit\n" * 45, encoding="utf-8")
    (root / "testcases").mkdir()
    (root / "testcases" / "x.json").write_text('{"needle": 1}\n', encoding="utf-8")
    (root / "oh_modules").mkdir()
    (root / "oh_modules" / "y.ets").write_text("needle\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle secret\n", encoding="utf-8")
    (outside / "S.ets").write_text("needle secret\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("needle secret\n", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    return root


def test_read_file_numbers_and_caps_lines(app):
    out = AppTools(app).read_file("a/A.ets", 1, 300).splitlines()
    assert len(out) == 150 and out[0] == "1: line 1" and out[-1] == "150: line 150"


def test_read_file_range_outside_file(app):
    assert AppTools(app).read_file("a/A.ets", 400, 410) == "error: range outside file"


def test_paths_outside_app_are_rejected(app, tmp_path):
    t = AppTools(app)
    for p in ("../secret.txt", str(tmp_path / "secret.txt"), "link/secret.txt", "link/S.ets"):
        assert t.read_file(p, 1, 5).startswith("error: no such file")
        assert t.line_text(p, 1) is None
    assert "secret" not in t.search("needle")


def test_skip_dirs_are_invisible(app):
    t = AppTools(app)
    assert t.read_file("testcases/x.json", 1, 5).startswith("error: no such file")
    hits = t.search("needle")
    assert "oh_modules" not in hits and "testcases" not in hits and "a/A.ets:10: needle" in hits


def test_search_is_case_insensitive_and_filtered(app):
    hits = AppTools(app).search("author_2")
    assert "a/A.ets:7: let author = 'Author_2';" in hits and "README.md" not in hits


def test_search_path_contains(app):
    assert AppTools(app).search("needle", path_contains="h/") == "no matches"


def test_search_caps_hits_with_note(app):
    out = AppTools(app).search("hit").splitlines()
    assert len(out) == 41
    assert out[-1] == "(showing 40 of 45 hits; narrow the pattern or use path_contains)"


def test_bad_regex_and_unknown_tool_return_text(app):
    t = AppTools(app)
    assert t.search("(").startswith("error: bad regex")
    assert t.call("delete", {}) == "error: unknown tool"


def test_call_coerces_and_reports_bad_arguments(app):
    t = AppTools(app)
    assert t.call("read_file", {"path": "a/A.ets", "start_line": "7", "end_line": "7"}) == \
        "7: let author = 'Author_2';"
    assert t.call("read_file", {"path": "a/A.ets", "start_line": "x"}).startswith("error:")


def test_line_text_handles_crlf_and_bom(app):
    t = AppTools(app)
    assert t.line_text("b/B.ets", 1) == "a" and t.line_text("b/B.ets", 2) == "b"
    assert t.line_text("b/B.ets", 3) is None and t.line_text("b/B.ets", 0) is None


def test_validate_submit_coerces_and_rejects():
    ok, err = validate_submit({**FULL_SUBMIT, "line": "48", "extra": 1})
    assert err is None and ok["line"] == 48 and "extra" not in ok
    assert validate_submit({**FULL_SUBMIT, "hypothesis": "maybe"})[1]
    assert validate_submit({**FULL_SUBMIT, "evidence_line": "4.5"})[1]
    assert validate_submit({**FULL_SUBMIT, "line": True})[1]
    missing = {k: v for k, v in FULL_SUBMIT.items() if k != "evidence_quote"}
    assert "evidence_quote" in validate_submit(missing)[1]


def test_schemas_are_mcp_shaped():
    names = [s["name"] for s in TOOL_SCHEMAS]
    assert names == ["read_file", "search", "submit"]
    assert all(s["inputSchema"]["type"] == "object" for s in TOOL_SCHEMAS)
    assert len(TOOL_SCHEMAS[2]["inputSchema"]["required"]) == 11
