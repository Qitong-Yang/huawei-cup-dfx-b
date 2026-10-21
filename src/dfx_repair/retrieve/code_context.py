"""Light-weight source structure helpers using position-preserving lexical masking.

Function boundaries remain approximate (including ArkUI layout blocks), but braces
in literals/comments cannot accidentally close a function. Unsupported headers yield
no span instead of a fabricated function boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lexical import mask_non_code, matching_braces

_HEADER_RE = re.compile(
    r"^\s*(?:export\s+)?(?:(?:public|private|protected|static|async|override|get|set)\s+)*"
    r"(?:function\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*(?:=\s*(?:async\s*)?)?"
    r"\(.*\)\s*(?::\s*[^={]+)?(?:=>\s*)?\{\s*$"
)
_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "else", "return", "do", "try"})
_MAX_HEADER_LINES = 6


@dataclass(frozen=True)
class FunctionSpan:
    name: str
    start_line: int
    end_line: int


def enclosing_function(lines: list[str], line_no: int) -> FunctionSpan | None:
    """Return the innermost recognized function/block covering a valid source line."""
    if not 1 <= line_no <= len(lines):
        return None
    code = mask_non_code("\n".join(lines))
    clean_lines = code.split("\n")
    pairs = matching_braces(code)
    spans: list[FunctionSpan] = []
    for opening, closing in pairs.items():
        open_line = code.count("\n", 0, opening)
        close_line = code.count("\n", 0, closing)
        if not open_line <= line_no - 1 <= close_line:
            continue
        start_of_line = code.rfind("\n", 0, opening) + 1
        prefix = code[start_of_line:opening + 1]
        for k in range(_MAX_HEADER_LINES):
            start = open_line - k
            if start < 0:
                break
            if start < open_line and any(ch in clean_lines[start] for ch in "{};"):
                break
            header = " ".join([*clean_lines[start:open_line], prefix])
            match = _HEADER_RE.fullmatch(header)
            if match and match.group("name") not in _KEYWORDS:
                spans.append(FunctionSpan(match.group("name"), start + 1, close_line + 1))
                break
    return min(spans, key=lambda s: (s.end_line - s.start_line, -s.start_line)) if spans else None
