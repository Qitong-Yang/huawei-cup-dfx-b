"""Light-weight ArkTS code structure helpers (no full parser).

Finds the function or method that encloses a line by scanning upward for a header and
matching braces forward. Good enough for scoring "same function" hits and for picking
code windows; not a replacement for a real AST.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# method(...) {, function f(...) {, name = (...) => {, get x() {, aboutToAppear(): void {
_HEADER_RE = re.compile(
    r"^\s*(?:export\s+)?(?:(?:public|private|protected|static|async|override|get|set)\s+)*"
    r"(?:function\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*(?:=\s*(?:async\s*)?)?\(.*\)\s*(?::\s*[^={]+)?(?:=>\s*)?\{\s*$"
)
_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "else", "return", "do", "try"})


@dataclass(frozen=True)
class FunctionSpan:
    name: str
    start_line: int  # 1-based, header line
    end_line: int  # 1-based, closing brace line


def _brace_end(lines: list[str], start_idx: int) -> int | None:
    depth = 0
    for i in range(start_idx, len(lines)):
        code = re.sub(r"//.*$", "", lines[i])
        depth += code.count("{") - code.count("}")
        if depth <= 0 and i > start_idx:
            return i
        if depth <= 0 and "{" in code and "}" in code and i == start_idx:
            return i
    return None


_MAX_HEADER_LINES = 6  # parameters may be wrapped over several lines


def _header_at(lines: list[str], i: int) -> tuple[str, int] | None:
    """If a function header ends on line i, return (name, header start index)."""
    if not lines[i].rstrip().endswith("{"):
        return None
    for k in range(_MAX_HEADER_LINES):
        start = i - k
        if start < 0:
            break
        if any(ch in lines[start] for ch in "{};") and start != i:
            break  # a previous statement or block ends here; the header cannot extend past it
        joined = " ".join(line.strip() for line in lines[start:i + 1])
        match = _HEADER_RE.match(joined)
        if match and match.group("name") not in _KEYWORDS:
            return match.group("name"), start
    return None


def enclosing_function(lines: list[str], line_no: int) -> FunctionSpan | None:
    """Return the innermost function-like block containing 1-based line_no.

    ArkUI layout blocks such as ``Row() {`` also count as blocks; callers that need a
    real method can walk outward from the returned span.
    """
    target = line_no - 1
    for i in range(min(target, len(lines) - 1), -1, -1):
        header = _header_at(lines, i)
        if header is None:
            continue
        end = _brace_end(lines, i)
        if end is not None and end >= target:
            name, start = header
            return FunctionSpan(name, start + 1, end + 1)
    return None
