"""Build resources/cases.json: one entry per (fault id, app) case in the contest dataset.

Usage:
    uv run python -m eval.inventory [--data-root data-master/data_B] [--out resources/cases.json]

The dataset layout is inconsistent (see plan/notes.md), so every log is checked by content:
its module name is compared with the app bundle, and its top business frame is resolved
against the app source tree. The output records evidence; it does not hide anomalies.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from dfx_repair.ingest import CrashContext, decode_log_bytes, parse_crash_log
from dfx_repair.retrieve import SourceIndex

from .paths import DEFAULT_CASES, DEFAULT_DATA_ROOT

logger = logging.getLogger(__name__)

TC_DIR_RE = re.compile(r"^(?P<tc>TC_JS\w*?_\d{3})-(?P<app>.+)$")
BUNDLE_RE = re.compile(r'"?bundleName"?\s*:\s*"([^"]+)"')
EXTRA_LOG_DIR = "DFXBenchMark_TC"  # B1 folder holding re-captured logs/scripts for other apps


@dataclass(frozen=True)
class LogRef:
    """A log file on disk, or a member inside a ZIP archive."""

    path: Path
    member: str | None = None

    def read_text(self) -> str:
        if self.member is None:
            return decode_log_bytes(self.path.read_bytes())
        with zipfile.ZipFile(self.path) as zf:
            return decode_log_bytes(zf.read(self.member))

    def label(self, root: Path) -> str:
        rel = self.path.relative_to(root).as_posix()
        return f"{rel}!{self.member}" if self.member else rel


def read_bundle(app_dir: Path) -> str:
    app_json = app_dir / "AppScope" / "app.json5"
    if not app_json.is_file():
        return ""
    match = BUNDLE_RE.search(app_json.read_text(encoding="utf-8", errors="replace"))
    return match.group(1) if match else ""


def faultlogger_logs(tc_dir: Path) -> list[LogRef]:
    return [LogRef(p) for p in sorted((tc_dir / "faultlogger").glob("*.log"))]


def zip_logs(zip_path: Path) -> tuple[list[LogRef], list[str]]:
    """Return .log members and the names of non-log members (dataset anomalies)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    logs = [LogRef(zip_path, n) for n in names if n.lower().endswith(".log")]
    others = [n for n in names if not n.endswith("/") and not n.lower().endswith(".log")]
    return logs, others


def find_scripts(testcase_dir: Path, tc: str) -> list[Path]:
    exact = sorted(testcase_dir.glob(f"{tc}.py"))
    if exact:
        return exact
    # WebShortDrama1: log dir TC_JSRangeError_024 but script TC_JSRangeError_001.py
    scripts = sorted(testcase_dir.glob("TC_*.py"))
    return scripts if len(scripts) == 1 else []


def iter_b12_cases(data_root: Path) -> Iterator[dict]:
    extra_root = data_root / "data_B1" / EXTRA_LOG_DIR
    for part in ("data_B1", "data_B2"):
        for app_dir in sorted((data_root / part).iterdir()):
            if not app_dir.is_dir() or app_dir.name == EXTRA_LOG_DIR:
                continue
            tc_dirs = [d for d in sorted(app_dir.iterdir()) if d.is_dir() and TC_DIR_RE.match(d.name)]
            if not tc_dirs:
                logger.info("skip %s: no fault log directory", app_dir.name)
                continue
            for tc_dir in tc_dirs:
                tc = TC_DIR_RE.match(tc_dir.name).group("tc")
                logs = faultlogger_logs(tc_dir)
                # extra copies: <app>/testcases/<tc dir>/ and DFXBenchMark_TC/<tc dir>/
                logs += faultlogger_logs(app_dir / "testcases" / tc_dir.name)
                extra_dir = extra_root / f"{tc}-{app_dir.name}"
                logs += faultlogger_logs(extra_dir)
                scripts = find_scripts(app_dir / "testcases", tc)
                alt_script = extra_root / f"{tc}.py"
                yield {
                    "dataset": part.replace("data_", ""),
                    "tc": tc,
                    "app": app_dir.name,
                    "app_dir": app_dir,
                    "log_dir_name": tc_dir.name,
                    "logs": logs,
                    "scripts": scripts,
                    "alt_scripts": [alt_script] if alt_script.is_file() else [],
                    "anomalies": [],
                }


def iter_b3_cases(data_root: Path) -> Iterator[dict]:
    bench = data_root / "data_B3" / "benchmarkfordfx-better"
    for zip_path in sorted((bench / "Logs" / "故障现场").glob("*.zip")):
        match = TC_DIR_RE.match(zip_path.stem)
        if not match:
            logger.warning("unrecognized zip name: %s", zip_path.name)
            continue
        logs, others = zip_logs(zip_path)
        anomalies = [f"zip contains non-log file: {n}" for n in others]
        script = bench / "HypiumProject" / "testcases" / f"{match.group('tc')}.py"
        yield {
            "dataset": "B3",
            "tc": match.group("tc"),
            "app": match.group("app"),
            "app_dir": bench / "App" / match.group("app"),
            "log_dir_name": zip_path.stem,
            "logs": logs,
            "scripts": [script] if script.is_file() else [],
            "alt_scripts": [],
            "anomalies": anomalies,
        }


def describe_log(ref: LogRef, ctx: CrashContext, index: SourceIndex, bundle: str, root: Path) -> dict:
    top = ctx.top_business_frame
    mapping = index.resolve(top.path) if top else None
    return {
        "path": ref.label(root),
        "module_name": ctx.module_name,
        "bundle_match": bool(bundle) and ctx.module_name == bundle,
        "error": f"{ctx.error_name}: {ctx.error_message}",
        "top_frame": f"{top.path}:{top.line}:{top.col}" if top else None,
        "top_frame_map": mapping.status.value if mapping else None,
        "top_frame_candidates": list(mapping.candidates) if mapping else [],
        "timestamp": ctx.timestamp,
        "standard_format": ctx.is_standard_format,
        "warnings": list(ctx.warnings),
    }


def _log_rank(log: dict) -> tuple:
    """Higher is better: maps to source, real faultlogger format, bundle matches, newest."""
    return (log["top_frame_map"] == "UNIQUE", log["standard_format"], log["bundle_match"],
            log["timestamp"])


def _signature(log: dict) -> tuple:
    top_file = (log["top_frame"] or "").rsplit(":", 2)[0].rsplit("/", 1)[-1]
    return (log["error"].lower(), top_file)


def choose_primary(logs: list[dict]) -> tuple[int | None, list[str]]:
    """Pick the primary log and flag logs whose error/top-frame file disagree with it."""
    if not logs:
        return None, []
    primary = max(range(len(logs)), key=lambda i: _log_rank(logs[i]))
    ref = _signature(logs[primary])
    notes = [
        f"log disagrees with primary (error/top file): {log['path']}"
        for i, log in enumerate(logs)
        if i != primary and log["top_frame"] and _signature(log) != ref
    ]
    return primary, notes


def build_inventory(data_root: Path) -> list[dict]:
    data_root = Path(data_root).resolve()
    if not data_root.is_dir():
        raise FileNotFoundError(f"data root does not exist: {data_root}")
    root = data_root
    cases: list[dict] = []
    indexes: dict[Path, SourceIndex] = {}
    for raw in [*iter_b12_cases(data_root), *iter_b3_cases(data_root)]:
        app_dir: Path = raw["app_dir"]
        if app_dir not in indexes:
            indexes[app_dir] = SourceIndex(app_dir)
            logger.info("indexed %s: %d source files", app_dir.name, indexes[app_dir].file_count)
        bundle = read_bundle(app_dir)
        # DFXBenchMark_TC reuses TC numbers across apps; keep a script only if it names our bundle
        raw["alt_scripts"] = [
            p for p in raw["alt_scripts"]
            if bundle and bundle in p.read_text(encoding="utf-8", errors="replace")
        ]
        logs = []
        for ref in raw["logs"]:
            ctx = parse_crash_log(ref.read_text())
            logs.append(describe_log(ref, ctx, indexes[app_dir], bundle, root))

        anomalies = list(raw["anomalies"])
        primary, disagreements = choose_primary(logs)
        anomalies += disagreements
        if not logs:
            anomalies.append("no crash log")
        elif not any(log["top_frame_map"] == "UNIQUE" for log in logs):
            anomalies.append("no log whose top business frame maps uniquely to app source")
        if raw["log_dir_name"].split("-", 1)[1] != raw["app"]:
            anomalies.append(f"log dir name '{raw['log_dir_name']}' does not match app '{raw['app']}'")
        if not raw["scripts"]:
            anomalies.append("no test script")

        cases.append({
            "case_id": f"{raw['tc']}@{raw['app']}@{raw['dataset']}",
            "dataset": raw["dataset"],
            "tc": raw["tc"],
            "app": raw["app"],
            "app_dir": app_dir.relative_to(root).as_posix(),
            "bundle": bundle,
            "primary_log": primary,
            "logs": logs,
            "scripts": [p.relative_to(root).as_posix() for p in raw["scripts"]],
            "alt_scripts": [p.relative_to(root).as_posix() for p in raw["alt_scripts"]],
            "anomalies": anomalies,
        })
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cases = build_inventory(args.data_root)
    payload = {"data_root": args.data_root.as_posix(), "case_count": len(cases), "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %d cases to %s", len(cases), args.out)


if __name__ == "__main__":
    main()
