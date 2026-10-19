"""Build the diagnosis-side sandbox for one case: allow-listed source + sanitized crash log.

The diagnosis side (locator, LLM) only ever sees the sandbox. Test scripts, injection reports,
ground truth and hand-written "Root cause"/"Fix" log sections never enter it.

Layout:
    <root>/<case slug>/app/...          allow-listed source files
    <root>/<case slug>/crash.log        primary log, sanitized
    <root>/<case slug>/manifest.json    case id, source hash, file count, leak-scan result
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from dfx_repair.ingest import decode_log_bytes, strip_leaky_sections
from dfx_repair.retrieve.source_map import SKIP_DIRS

logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = (".ets", ".ts", ".js", ".json5", ".json")
# Folders that hold tests, reports or fault documentation in this dataset.
DENIED_DIRS = SKIP_DIRS | {"docs", "HypiumProject", "Logs", "tests", "snapshots"}
# Strings that would reveal the answer if they appeared on the diagnosis side.
LEAK_PATTERNS = re.compile(r"fault_file|fault_line|故障位置|崩溃根因|root_cause|TC_JS\w*Error_\d{3}")


@dataclass(frozen=True)
class Sandbox:
    case_id: str
    root: Path
    app_dir: Path
    log_path: Path
    source_hash: str
    file_count: int
    leaks: tuple[str, ...]


def case_slug(case_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)


def _is_denied_dir(name: str) -> bool:
    return name in DENIED_DIRS or name.startswith("TC_")


def copy_allowlisted(src: Path, dst: Path) -> tuple[str, int]:
    """Copy allow-listed files; return (sha256 over sorted path+content, file count)."""
    entries: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if not _is_denied_dir(d))
        rel_dir = Path(dirpath).relative_to(src)
        for name in sorted(filenames):
            if name.endswith(ALLOWED_SUFFIXES):
                entries.append(((rel_dir / name).as_posix(), Path(dirpath) / name))

    digest = hashlib.sha256()
    for rel, path in sorted(entries):
        data = path.read_bytes()
        digest.update(rel.encode("utf-8") + b"\0" + data + b"\0")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return digest.hexdigest()[:16], len(entries)


def read_log(data_root: Path, label: str) -> str:
    """Read a log by its inventory label ("path" or "zip!member")."""
    if "!" in label:
        zip_rel, member = label.split("!", 1)
        with zipfile.ZipFile(data_root / zip_rel) as zf:
            return decode_log_bytes(zf.read(member))
    return decode_log_bytes((data_root / label).read_bytes())


def scan_leaks(root: Path) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name != "manifest.json":
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in LEAK_PATTERNS.finditer(text):
                hits.append(f"{path.relative_to(root).as_posix()}: {match.group(0)}")
    return hits


def build_sandbox(case: dict, data_root: Path, root: Path, rebuild: bool = False) -> Sandbox:
    """Create (or reuse) the sandbox for one inventory case."""
    case_dir = root / case_slug(case["case_id"])
    manifest_path = case_dir / "manifest.json"
    if manifest_path.is_file() and not rebuild:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        return Sandbox(m["case_id"], case_dir, case_dir / "app", case_dir / "crash.log",
                       m["source_hash"], m["file_count"], tuple(m["leaks"]))

    if case_dir.exists():
        shutil.rmtree(case_dir)
    app_dir = case_dir / "app"
    source_hash, count = copy_allowlisted(data_root / case["app_dir"], app_dir)

    log_label = case["logs"][case["primary_log"]]["path"]
    log_path = case_dir / "crash.log"
    log_path.write_text(strip_leaky_sections(read_log(data_root, log_label)), encoding="utf-8")

    leaks = scan_leaks(case_dir)
    if leaks:
        logger.warning("%s: %d possible answer leaks in sandbox, e.g. %s", case["case_id"], len(leaks), leaks[0])
    manifest = {"case_id": case["case_id"], "source_hash": source_hash, "file_count": count,
                "log": log_label, "leaks": leaks}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return Sandbox(case["case_id"], case_dir, app_dir, log_path, source_hash, count, tuple(leaks))
