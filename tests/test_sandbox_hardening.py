"""Integrity, redaction and ownership tests for diagnosis-side input copies."""

import json
import warnings
import zipfile
from pathlib import Path

import pytest
from eval import sandbox
from eval.sandbox import AnswerLeakError, SandboxError, build_sandbox, read_log

from dfx_repair.ingest import parse_crash_log, strip_leaky_sections


def _dataset(tmp_path: Path):
    data = tmp_path / "data"
    app = data / "app"
    app.mkdir(parents=True)
    source = app / "Main.ets"
    source.write_text("const version = 1;\n", encoding="utf-8")
    log = data / "crash.log"
    log.write_text("Error name:TypeError\nError message:original\n", encoding="utf-8")
    case = {"case_id": "C", "app_dir": "app", "primary_log": 0,
            "logs": [{"path": "crash.log"}]}
    return data, source, log, case, tmp_path / "boxes"


@pytest.mark.parametrize("heading", ["Root cause:", "root CAUSE: secret", "Fix: secret", "FIX：secret"])
def test_answer_body_is_removed_until_a_recognized_header(heading):
    text = ("Error name:TypeError\n" + heading + "\nSECRET\nUnknown: SECRET\n===\n"
            "Stacktrace:\n at main (Main.ets:1:1)\n")
    cleaned = strip_leaky_sections(text)
    assert "SECRET" not in cleaned and "secret" not in cleaned
    assert "Stacktrace:" in cleaned and parse_crash_log(cleaned).top_business_frame is not None


def test_trailing_answer_body_without_indent_does_not_reappear():
    assert strip_leaky_sections("Fix:\nSECRET\n\nPlain unindented prose\n") == ""


def test_cache_reuses_unchanged_copy_and_has_complete_identity(tmp_path):
    data, _, log, case, root = _dataset(tmp_path)
    log.write_bytes(b"Error name:TypeError\r\nError message:original\r\n")
    first = build_sandbox(case, data, root)
    manifest_path = first.root / "manifest.json"
    before = manifest_path.stat().st_mtime_ns
    second = build_sandbox(case, data, root)
    assert manifest_path.stat().st_mtime_ns == before
    assert second == first
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert all(len(manifest[k]) == 64 for k in ["source_digest", "raw_log_hash", "log_hash", "policy_hash"])


def test_cache_invalidates_after_source_and_log_changes(tmp_path):
    data, source, log, case, root = _dataset(tmp_path)
    first = build_sandbox(case, data, root)
    source.write_text("const version = 2;\n", encoding="utf-8")
    log.write_text("Error name:RangeError\n", encoding="utf-8")
    second = build_sandbox(case, data, root)
    assert second.source_hash != first.source_hash and second.log_hash != first.log_hash
    assert "version = 2" in (second.app_dir / "Main.ets").read_text(encoding="utf-8")
    assert second.log_path.read_text(encoding="utf-8") == "Error name:RangeError\n"


def test_cache_invalidates_on_policy_change(tmp_path, monkeypatch):
    data, _, _, case, root = _dataset(tmp_path)
    first = build_sandbox(case, data, root)
    monkeypatch.setattr(sandbox, "REDACTION_VERSION", "test-policy-next")
    second = build_sandbox(case, data, root)
    assert first.policy_hash != second.policy_hash


def test_cache_checks_actual_copy_not_only_manifest(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    first = build_sandbox(case, data, root)
    (first.app_dir / "Main.ets").write_text("tampered", encoding="utf-8")
    (first.app_dir / "unexpected.json").write_text("{}", encoding="utf-8")
    first.log_path.write_text("tampered", encoding="utf-8")
    second = build_sandbox(case, data, root)
    assert "version = 1" in (second.app_dir / "Main.ets").read_text(encoding="utf-8")
    assert not (second.app_dir / "unexpected.json").exists()
    assert "original" in second.log_path.read_text(encoding="utf-8")


def test_cache_removes_source_files_deleted_from_input(tmp_path):
    data, source, _, case, root = _dataset(tmp_path)
    other = source.with_name("Other.ets")
    other.write_text("const other = 1;", encoding="utf-8")
    first = build_sandbox(case, data, root)
    other.unlink()
    second = build_sandbox(case, data, root)
    assert second.source_hash != first.source_hash
    assert not (second.app_dir / "Other.ets").exists()


def test_old_owned_manifest_is_upgraded_after_integrity_checks(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    first = build_sandbox(case, data, root)
    manifest = first.root / "manifest.json"
    old = json.loads(manifest.read_text(encoding="utf-8"))
    old = {k: old[k] for k in ["case_id", "source_hash", "file_count", "log", "leaks"]}
    manifest.write_text(json.dumps(old), encoding="utf-8")
    second = build_sandbox(case, data, root)
    upgraded = json.loads(manifest.read_text(encoding="utf-8"))
    assert second.source_hash == first.source_hash
    assert upgraded["schema"] == sandbox.CACHE_SCHEMA
    assert len(upgraded["source_digest"]) == 64


def test_case_id_alone_is_not_sufficient_ownership_for_deletion(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    target = root / "C"
    target.mkdir(parents=True)
    manifest = target / "manifest.json"
    manifest.write_text(json.dumps({"case_id": "C"}), encoding="utf-8")
    with pytest.raises(SandboxError, match="provenance"):
        build_sandbox(case, data, root)
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"case_id": "C"}


def test_source_with_answer_marker_blocks_publication(tmp_path):
    data, source, _, case, root = _dataset(tmp_path)
    source.write_text("// fault_file: Main.ets\n", encoding="utf-8")
    with pytest.raises(AnswerLeakError):
        build_sandbox(case, data, root)
    assert not (root / "C").exists()
    assert not list(root.glob(".C.tmp-*"))



@pytest.mark.parametrize("code", ["const rootCause = analyze(e);\n", "const actions = {\n  fix: () => retry(),\n};\n",
                                  "// TODO: root cause unknown\n"])
def test_ordinary_source_identifiers_are_not_answer_markers(tmp_path, code):
    data, source, _, case, root = _dataset(tmp_path)
    source.write_text(code, encoding="utf-8")
    assert build_sandbox(case, data, root).leaks == ()


def test_log_answer_wording_still_blocks_publication(tmp_path):
    data, _, log, case, root = _dataset(tmp_path)
    log.write_text("Error name:TypeError\nError message:root cause is the index\n", encoding="utf-8")
    with pytest.raises(AnswerLeakError):
        build_sandbox(case, data, root)

def test_unknown_existing_directory_is_never_deleted(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    target = root / "C"
    target.mkdir(parents=True)
    user_file = target / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")
    with pytest.raises(SandboxError, match="unowned"):
        build_sandbox(case, data, root)
    assert user_file.read_text(encoding="utf-8") == "user data"


def test_unexpected_top_level_file_in_owned_copy_is_preserved(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    first = build_sandbox(case, data, root)
    user_file = first.root / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")
    with pytest.raises(SandboxError, match="unexpected"):
        build_sandbox(case, data, root)
    assert user_file.exists()


def test_case_slug_collision_is_rejected_without_deleting_first_copy(tmp_path):
    data, _, _, case, root = _dataset(tmp_path)
    case["case_id"] = "A@B"
    first = build_sandbox(case, data, root)
    second_case = {**case, "case_id": "A#B"}
    with pytest.raises(SandboxError, match="different case"):
        build_sandbox(second_case, data, root)
    assert (first.app_dir / "Main.ets").exists()


@pytest.mark.parametrize("unsafe", ["../outside", "/outside", "C:/outside"])
def test_relative_source_path_cannot_escape_data_root(tmp_path, unsafe):
    data, _, _, case, root = _dataset(tmp_path)
    case["app_dir"] = unsafe
    with pytest.raises(SandboxError):
        build_sandbox(case, data, root)


def test_sandbox_root_must_not_overlap_data(tmp_path):
    data, _, _, case, _ = _dataset(tmp_path)
    with pytest.raises(SandboxError, match="overlap"):
        build_sandbox(case, data, data / "boxes")


def test_zip_member_slash_variants_are_supported_but_ambiguity_rejected(tmp_path):
    archive = tmp_path / "logs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("case/faultlogger/crash.log", "Error name:TypeError\n")
    assert read_log(tmp_path, "logs.zip!case\\faultlogger\\crash.log") == "Error name:TypeError\n"
    with warnings.catch_warnings():
        # Windows ZipInfo normalizes backslashes, deliberately creating a duplicate.
        warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr("case\\faultlogger\\crash.log", "other")
    with pytest.raises(SandboxError, match="uniquely"):
        read_log(tmp_path, "logs.zip!case/faultlogger/crash.log")


def test_page_history_allows_blank_lines_from_utf16_newlines():
    ctx = parse_crash_log("Page switch history:\n\n  12:00:00.123 /Page\n\nReason:TypeError\n")
    assert ctx.page_history == ("/Page",)
