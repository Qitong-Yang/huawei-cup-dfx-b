"""Map stack-frame paths to files in an app source tree.

A frame path can be a full module path (``components/x/src/main/ets/A.ets``) or a bare file
name (``A.ets``). Resolution matches the longest common trailing path and reports whether the
match is unique, so callers never silently pick one of several same-named files.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE_EXTS = (".ets", ".ts", ".js")
# Build outputs, dependencies and IDE folders: never the location of an injected fault.
SKIP_DIRS = frozenset({
    "oh_modules", "node_modules", "build", ".hvigor", ".idea", ".git", "hvigor",
    "ohosTest", "__pycache__", "testcases", "reports", "screenshots",
})


class MapStatus(str, Enum):
    UNIQUE = "UNIQUE"
    MULTIPLE = "MULTIPLE"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class SourceMapping:
    frame_path: str
    status: MapStatus
    candidates: tuple[str, ...]  # paths relative to the app root, best matches only
    matched_parts: int  # number of trailing path components shared with the frame path


class SourceIndex:
    """Index of source files under one app root, keyed by file name."""

    def __init__(self, app_root: Path) -> None:
        self.app_root = Path(app_root).resolve(strict=True)
        if not self.app_root.is_dir():
            raise ValueError(f"app source root is not a directory: {app_root}")
        self._by_name: dict[str, list[str]] = {}
        for dirpath, dirnames, filenames in os.walk(self.app_root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                                  and not (Path(dirpath) / d).is_symlink())
            rel_dir = Path(dirpath).relative_to(self.app_root)
            for name in filenames:
                source = Path(dirpath) / name
                if name.endswith(SOURCE_EXTS) and not source.is_symlink() \
                        and source.resolve().is_relative_to(self.app_root):
                    self._by_name.setdefault(name, []).append((rel_dir / name).as_posix())

    @property
    def file_count(self) -> int:
        return sum(len(v) for v in self._by_name.values())

    def resolve(self, frame_path: str) -> SourceMapping:
        """Resolve one normalized frame path."""
        parts = [p for p in frame_path.split("/") if p]
        if not parts:
            return SourceMapping(frame_path, MapStatus.NOT_FOUND, (), 0)
        candidates = self._by_name.get(parts[-1], [])
        if not candidates:
            return SourceMapping(frame_path, MapStatus.NOT_FOUND, (), 0)

        scored = [(_common_suffix_len(parts, c.split("/")), c) for c in candidates]
        best = max(score for score, _ in scored)
        best_paths = tuple(sorted(c for score, c in scored if score == best))
        status = MapStatus.UNIQUE if len(best_paths) == 1 else MapStatus.MULTIPLE
        return SourceMapping(frame_path, status, best_paths, best)


def _common_suffix_len(a: list[str], b: list[str]) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n
