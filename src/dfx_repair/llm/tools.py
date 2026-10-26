"""The LLM's tools (spec 4.4): read_file, search and submit, confined to one app root.

Paths are relative to the app root. Anything that resolves outside it, passes through a
symlink, or lies under a build/test folder (``SKIP_DIRS``) is reported as missing. Searchable
files are read once, so each search runs in memory. Result texts match ``temp/spike_llm.py``
v4, the version the prompt was frozen against.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..retrieve.source_map import SKIP_DIRS

logger = logging.getLogger(__name__)

SEARCH_SUFFIXES = frozenset({".ets", ".ts", ".js", ".json", ".json5"})
NO_SUCH_FILE = "error: no such file (paths are relative to the app root)"
LINE_FIELDS = ("line", "evidence_line", "origin_line")
SUBMIT_FIELDS = (
    "hypothesis", "file", "line", "quoted_line",
    "evidence_file", "evidence_line", "evidence_quote",
    "origin_file", "origin_line", "origin_quote", "reasoning",
)
HYPOTHESES = ("crash_line", "upstream")

_STR = {"type": "string"}
_INT = {"type": "integer"}
TOOL_SCHEMAS: tuple[dict, ...] = (
    {"name": "read_file", "description": "Read numbered lines of an app file.",
     "inputSchema": {"type": "object",
                     "properties": {"path": _STR, "start_line": _INT, "end_line": _INT},
                     "required": ["path", "start_line", "end_line"]}},
    {"name": "search",
     "description": "Case-insensitive regex search over app source files; returns path:line: text. "
                    "Optional path_contains narrows to files whose path contains that substring.",
     "inputSchema": {"type": "object",
                     "properties": {"pattern": _STR, "path_contains": _STR},
                     "required": ["pattern"]}},
    {"name": "submit", "description": "Submit the final root-cause location.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "hypothesis": {"type": "string", "enum": list(HYPOTHESES)},
                         **{f: (_INT if f in LINE_FIELDS else _STR)
                            for f in SUBMIT_FIELDS if f != "hypothesis"}},
                     "required": list(SUBMIT_FIELDS)}},
)


def _numbered(lines: list[str], start: int, end: int) -> str:
    start, end = max(1, start), min(len(lines), end)
    return "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


class AppTools:
    """read_file/search over one app source tree."""

    def __init__(self, app_root: Path, read_limit: int = 150, search_limit: int = 40) -> None:
        self.app_root = Path(app_root).resolve(strict=True)
        self.read_limit = read_limit
        self.search_limit = search_limit
        self._cache: dict[Path, list[str]] = {}
        self._searchable: list[tuple[str, list[str]]] | None = None

    def _search_files(self) -> list[tuple[str, list[str]]]:
        """All searchable files, read on the first search only (a scan is slow on /mnt)."""
        if self._searchable is None:
            found: list[tuple[str, list[str]]] = []
            for dirpath, dirnames, filenames in os.walk(self.app_root):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                                      and not (Path(dirpath) / d).is_symlink())
                for name in filenames:
                    path = Path(dirpath) / name
                    resolved = self._resolve_path(path) if path.suffix in SEARCH_SUFFIXES else None
                    if resolved is not None:
                        rel = path.relative_to(self.app_root).as_posix()
                        found.append((rel, self._lines(resolved)))
            self._searchable = sorted(found)
        return self._searchable

    def _resolve_path(self, path: Path) -> Path | None:
        """Real file inside the root, reached without symlinks and outside SKIP_DIRS."""
        resolved = path.resolve()
        if Path(os.path.normpath(path)) != resolved or not resolved.is_file():
            return None  # a symlink was crossed, or no such file
        if not resolved.is_relative_to(self.app_root):
            return None
        if any(part in SKIP_DIRS for part in resolved.relative_to(self.app_root).parts[:-1]):
            return None
        return resolved

    def file_lines(self, rel: str) -> list[str] | None:
        """Lines of an app file, or None when it is missing or not visible to the model."""
        if not rel:
            return None
        path = self._resolve_path(self.app_root / rel)
        return None if path is None else self._lines(path)

    def _lines(self, path: Path) -> list[str]:
        if path not in self._cache:
            self._cache[path] = _read_lines(path)
        return self._cache[path]

    def read_file(self, path: str, start_line: int, end_line: int) -> str:
        lines = self.file_lines(path)
        if lines is None:
            return NO_SUCH_FILE
        end = min(end_line, start_line + self.read_limit - 1)
        return _numbered(lines, start_line, end) or "error: range outside file"

    def search(self, pattern: str, path_contains: str = "") -> str:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"error: bad regex {exc}"
        hits: list[str] = []
        total = 0
        for rel, lines in self._search_files():
            if path_contains not in rel:
                continue
            for n, text in enumerate(lines, 1):
                if rx.search(text):
                    total += 1
                    if len(hits) < self.search_limit:
                        hits.append(f"{rel}:{n}: {text.strip()[:160]}")
        if total > len(hits):
            hits.append(f"(showing {len(hits)} of {total} hits; "
                        "narrow the pattern or use path_contains)")
        return "\n".join(hits) or "no matches"

    def line_text(self, path: str, line: int) -> str | None:
        """Raw text of one line, or None when the file is not readable or the line is absent."""
        lines = self.file_lines(path)
        if lines is None or not 1 <= line <= len(lines):
            return None
        return lines[line - 1]

    def call(self, name: str, args: dict) -> str:
        """Run read_file or search with model-supplied arguments; errors come back as text."""
        try:
            if name == "read_file":
                start = int(args.get("start_line", 1))
                return self.read_file(str(args.get("path", "")), start,
                                      int(args.get("end_line", start)))
            if name == "search":
                return self.search(str(args.get("pattern", "")),
                                   str(args.get("path_contains") or ""))
        except (TypeError, ValueError) as exc:
            return f"error: bad arguments {exc}"
        return "error: unknown tool"


def _as_line(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def validate_submit(args: dict) -> tuple[dict | None, str | None]:
    """Check a submit call; return (answer with only SUBMIT_FIELDS, None) or (None, error text)."""
    missing = [f for f in SUBMIT_FIELDS if f not in args]
    if missing:
        return None, f"error: missing fields: {', '.join(missing)}"
    if args["hypothesis"] not in HYPOTHESES:
        return None, "error: hypothesis must be 'crash_line' or 'upstream'"
    answer: dict = {}
    for f in SUBMIT_FIELDS:
        value = args[f]
        if f in LINE_FIELDS:
            value = _as_line(value)
            if value is None:
                return None, f"error: {f} must be an integer"
        elif not isinstance(value, str):
            return None, f"error: {f} must be a string"
        answer[f] = value
    return answer, None
