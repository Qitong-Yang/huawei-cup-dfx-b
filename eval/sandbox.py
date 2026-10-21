"""Build version-bound diagnosis copies from allow-listed source and sanitized logs.

This is input preparation, not an operating-system security boundary. Cache reuse
checks input content, redaction policy and the on-disk copy; known answer markers
block use rather than merely warning. Never point its root at valuable user data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from dfx_repair.ingest import decode_log_bytes, strip_leaky_sections
from dfx_repair.retrieve.source_map import SKIP_DIRS

logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = (".ets", ".ts", ".js", ".json5", ".json")
DENIED_DIRS = SKIP_DIRS | {"docs", "HypiumProject", "Logs", "tests", "snapshots"}
LEAK_PATTERNS = re.compile(
    r"fault_file|fault_line|故障位置|崩溃根因|root[\s_-]*cause|"
    r"TC_JS\w*Error_\d{3}|^\s*fix\s*[:：]", re.IGNORECASE | re.MULTILINE,
)
# Source code legitimately uses names like ``rootCause`` or ``fix:`` properties, so
# copied source is only checked for markers that never occur in ordinary code.
SOURCE_LEAK_PATTERNS = re.compile(r"fault_file|fault_line|故障位置|崩溃根因|root_cause|TC_JS\w*Error_\d{3}")
CACHE_SCHEMA = 2
REDACTION_VERSION = "known-answer-sections-v3"


class SandboxError(ValueError):
    """Input or ownership checks prevented creating a reliable diagnosis copy."""


class AnswerLeakError(SandboxError):
    """Known answer markers remain in diagnosis-side input."""


@dataclass(frozen=True)
class Sandbox:
    case_id: str
    root: Path
    app_dir: Path
    log_path: Path
    source_hash: str  # legacy 16-character identifier; full digest is in manifest
    file_count: int
    leaks: tuple[str, ...]
    log_hash: str = ""
    policy_hash: str = ""


def case_slug(case_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)
    if not slug or slug in {".", ".."}:
        raise SandboxError("case id must have a non-empty safe directory name")
    return slug


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _unlinked_absolute(path: Path) -> Path:
    absolute = Path(path).absolute()
    for component in (absolute, *absolute.parents):
        if _linked(component):
            raise SandboxError(f"linked path component is not supported: {component}")
    return absolute.resolve()


def _within(root: Path, relative: str) -> Path:
    """Resolve a relative data path without permitting links or escaping root."""
    value = PurePosixPath(relative.replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts or any(":" in p for p in value.parts):
        raise SandboxError(f"unsafe relative input path: {relative}")
    candidate = root.joinpath(*value.parts)
    current = root
    for part in value.parts:
        current /= part
        if _linked(current):
            raise SandboxError(f"linked input path is not supported: {current}")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise SandboxError(f"input path escapes data root: {relative}")
    return candidate


def _is_denied_dir(name: str) -> bool:
    return name in DENIED_DIRS or name.startswith("TC_")


def _assert_plain_tree(root: Path) -> None:
    """Reject symlinks/junctions before reading, overwriting or deleting a tree."""
    if _linked(root):
        raise SandboxError(f"linked tree is not supported: {root}")
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(dirpath) / name
            if _linked(path):
                raise SandboxError(f"linked sandbox entry is not supported: {path}")


def _snapshot(src: Path) -> tuple[list[tuple[str, bytes]], str]:
    if not src.is_dir() or _linked(src):
        raise SandboxError(f"source directory is missing or linked: {src}")
    entries: list[tuple[str, bytes]] = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _is_denied_dir(d))
        for name in dirnames:
            if _linked(Path(dirpath) / name):
                raise SandboxError(f"linked source directory: {Path(dirpath) / name}")
        rel_dir = Path(dirpath).relative_to(src)
        for name in sorted(filenames):
            if not name.endswith(ALLOWED_SUFFIXES):
                continue
            path = Path(dirpath) / name
            if _linked(path):
                raise SandboxError(f"linked source file: {path}")
            entries.append(((rel_dir / name).as_posix(), path.read_bytes()))
    digest = hashlib.sha256()
    for rel, data in sorted(entries):
        digest.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return entries, digest.hexdigest()


def _write_snapshot(entries: list[tuple[str, bytes]], dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for rel, data in entries:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def copy_allowlisted(src: Path, dst: Path) -> tuple[str, int]:
    """Copy source files; keep the historic short identifier for API compatibility."""
    src, dst = _unlinked_absolute(src), _unlinked_absolute(dst)
    if dst.resolve().is_relative_to(src) or src.is_relative_to(dst.resolve()):
        raise SandboxError("source and destination must not overlap")
    if dst.exists():
        _assert_plain_tree(dst)
        if any(dst.iterdir()):
            raise SandboxError("copy destination must be empty")
    entries, digest = _snapshot(src)
    _write_snapshot(entries, dst)
    return digest[:16], len(entries)


def _normal_member(name: str) -> str:
    value = PurePosixPath(name.replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts or any(":" in p for p in value.parts):
        raise SandboxError(f"unsafe ZIP member: {name}")
    return value.as_posix()


def read_log(data_root: Path, label: str) -> str:
    """Read a relative file or ZIP member, accepting unambiguous slash variants."""
    data_root = _unlinked_absolute(data_root)
    if "!" in label:
        zip_rel, member = label.split("!", 1)
        with zipfile.ZipFile(_within(data_root, zip_rel)) as zf:
            target = _normal_member(member)
            matches = [i for i in zf.infolist() if not i.is_dir()
                       and _normal_member(i.filename) == target]
            if len(matches) != 1:
                raise SandboxError(f"ZIP member must match uniquely: {member} ({len(matches)} matches)")
            return decode_log_bytes(zf.read(matches[0]))
    return decode_log_bytes(_within(data_root, label).read_bytes())


def scan_leaks(root: Path) -> list[str]:
    _assert_plain_tree(root)
    hits: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name != "manifest.json":
            text = path.read_text(encoding="utf-8", errors="replace")
            patterns = LEAK_PATTERNS if path.suffix == ".log" else SOURCE_LEAK_PATTERNS
            hits.extend(f"{path.relative_to(root).as_posix()}: {m.group(0)}"
                        for m in patterns.finditer(text))
    return hits


def _policy_hash() -> str:
    policy = {"schema": CACHE_SCHEMA, "redaction": REDACTION_VERSION,
              "suffixes": ALLOWED_SUFFIXES, "denied": sorted(DENIED_DIRS),
              "leaks": LEAK_PATTERNS.pattern, "flags": LEAK_PATTERNS.flags,
              "source_leaks": SOURCE_LEAK_PATTERNS.pattern}
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()


def _remove_owned_case(case_dir: Path, root: Path, case_id: str) -> None:
    # Resolve final absolute paths before every recursive delete. Reject anything
    # outside this dedicated immediate case directory, links, or unknown contents.
    resolved_root, resolved_case = root.resolve(), case_dir.resolve()
    if resolved_case.parent != resolved_root or resolved_case == resolved_root:
        raise SandboxError("refusing recursive deletion outside the sandbox root")
    _assert_plain_tree(case_dir)
    manifest_path = case_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SandboxError(f"refusing to replace an unowned directory: {case_dir}")
    try:
        owner = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise SandboxError(f"cannot verify sandbox ownership: {case_dir}") from exc
    if not isinstance(owner, dict):
        raise SandboxError("cannot verify sandbox ownership from a non-object manifest")
    if owner.get("case_id") != case_id:
        raise SandboxError("sandbox directory belongs to a different case")
    legacy_fields = {"case_id", "source_hash", "file_count", "log", "leaks"}
    managed = (
        isinstance(owner, dict) and legacy_fields <= owner.keys()
        and isinstance(owner.get("source_hash"), str)
        and re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{64}", owner["source_hash"]) is not None
        and isinstance(owner.get("file_count"), int) and owner["file_count"] >= 0
        and isinstance(owner.get("log"), str) and isinstance(owner.get("leaks"), list)
    )
    if not managed:
        raise SandboxError("refusing to replace a manifest without managed-copy provenance")
    if {p.name for p in case_dir.iterdir()} - {"app", "crash.log", "manifest.json"}:
        raise SandboxError("refusing to delete unexpected sandbox files")
    shutil.rmtree(resolved_case)


def build_sandbox(case: dict, data_root: Path, root: Path, rebuild: bool = False) -> Sandbox:
    """Create a verified copy or reuse one only when both inputs and copy match."""
    data_root, root = _unlinked_absolute(data_root), _unlinked_absolute(root)
    if not data_root.is_dir() or _linked(root):
        raise SandboxError("data root must exist and sandbox root must not be linked")
    source = _within(data_root, case["app_dir"])
    # The destination may be a sibling of data, never inside source or above it.
    root_resolved = root.resolve()
    if root_resolved.is_relative_to(data_root) or data_root.is_relative_to(root_resolved):
        raise SandboxError("sandbox root and data root must not overlap")
    case_id = case["case_id"]
    case_dir = root / case_slug(case_id)
    if _linked(case_dir) or case_dir.resolve().parent != root_resolved:
        raise SandboxError("unsafe case destination")
    primary = case.get("primary_log")
    if not isinstance(primary, int) or isinstance(primary, bool) or not 0 <= primary < len(case["logs"]):
        raise SandboxError("case has no valid primary log")
    log_label = case["logs"][primary]["path"]
    raw_log = read_log(data_root, log_label)
    cleaned_log = strip_leaky_sections(raw_log).replace("\r\n", "\n").replace("\r", "\n")
    entries, digest = _snapshot(source)
    policy = _policy_hash()
    log_hash = hashlib.sha256(cleaned_log.encode("utf-8")).hexdigest()
    identity = {"schema": CACHE_SCHEMA, "case_id": case_id,
                "data_root": str(data_root), "app_dir": case["app_dir"],
                "source_digest": digest, "source_hash": digest[:16],
                "file_count": len(entries), "log": log_label,
                "raw_log_hash": hashlib.sha256(raw_log.encode("utf-8")).hexdigest(),
                "log_hash": log_hash, "policy_hash": policy, "leaks": []}
    manifest_path = case_dir / "manifest.json"
    if manifest_path.is_file() and not rebuild:
        _assert_plain_tree(case_dir)
        try:
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            same_identity = isinstance(stored, dict) and all(stored.get(k) == v for k, v in identity.items())
            # Check every copied file, including unexpected/deleted files, so a
            # locally edited sandbox cannot be mislabelled with the old hash.
            actual_files = {p.relative_to(case_dir / "app").as_posix(): p.read_bytes()
                            for p in (case_dir / "app").rglob("*") if p.is_file()}
            same_copy = actual_files == dict(entries)
            same_log = (case_dir / "crash.log").read_text(encoding="utf-8") == cleaned_log
            expected_layout = {"app", "crash.log", "manifest.json"}
            same_layout = {p.name for p in case_dir.iterdir()} == expected_layout
            if same_identity and same_copy and same_log and same_layout and not scan_leaks(case_dir):
                return Sandbox(case_id, case_dir, case_dir / "app", case_dir / "crash.log",
                               digest[:16], len(entries), (), log_hash, policy)
        except (ValueError, OSError):
            # An unreadable/corrupt manifest cannot establish ownership; the
            # replacement check below will refuse to delete it.
            pass

    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{case_slug(case_id)}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    # This marker makes cleanup verifiably owned even if copying or scanning fails.
    (temporary / "manifest.json").write_text(json.dumps(identity), encoding="utf-8")
    try:
        _write_snapshot(entries, temporary / "app")
        (temporary / "crash.log").write_text(cleaned_log, encoding="utf-8", newline="\n")
        leaks = scan_leaks(temporary)
        if leaks:
            raise AnswerLeakError(f"{case_id}: diagnosis input contains answer markers: {leaks[:3]}")
        if case_dir.exists():
            _remove_owned_case(case_dir, root, case_id)
        # Both final absolute endpoints are verified inside the same workspace root.
        if temporary.resolve().parent != root.resolve() or case_dir.resolve().parent != root.resolve():
            raise SandboxError("unsafe sandbox publication paths")
        temporary.rename(case_dir)
    finally:
        if temporary.exists():
            _remove_owned_case(temporary, root, case_id)
    return Sandbox(case_id, case_dir, case_dir / "app", case_dir / "crash.log",
                   digest[:16], len(entries), (), log_hash, policy)
