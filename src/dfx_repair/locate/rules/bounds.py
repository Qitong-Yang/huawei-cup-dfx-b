"""Out-of-bounds access: look for the boundary check or loop bound that let the index through.

R-BOUNDS-PARTIAL  `arr[i].p` reads undefined, and an earlier `if (...)` in the same function
                  checks `arr.length` for another index but not for `i`.
R-BOUNDS-LOOP     a buffer/view/index out-of-range error inside a loop whose condition is
                  `v <= total` (off-by-one).
"""

from __future__ import annotations

import re

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve import enclosing_function

from ..result import Location
from .base import CrashSite, RuleCandidate, message

_INDEXED_READ = re.compile(r"([\w$.]+)\s*\[\s*([\w$.]+)\s*\]\s*\.\s*$")
_UNDEFINED_READ = re.compile(r"Cannot read property '?[\w$]+'? of undefined", re.IGNORECASE)
_RANGE_ERRORS = re.compile(
    r"elementSize > viewSize|out-of-bounds|out of range|offset is outside the bounds", re.IGNORECASE)
_LOOP_LE = re.compile(r"\bfor\s*\(\s*(?:let|var|const)?\s*([\w$]+)\s*=.*;\s*\1\s*<=\s*([^;]+);")


def _function_range(site: CrashSite) -> tuple[int, int]:
    span = enclosing_function(list(site.lines), site.line)
    return (span.start_line, span.end_line) if span else (max(1, site.line - 40), site.line)


def partial_bounds_check(ctx: CrashContext, site: CrashSite) -> list[RuleCandidate]:
    if not _UNDEFINED_READ.search(message(ctx)):
        return []
    match = _INDEXED_READ.search(site.text[: max(site.col - 1, 0)])
    if not match:
        return []
    array, index = match.group(1), match.group(2)
    array_len = re.compile(rf"\b{re.escape(array)}\.length\b")
    start, _ = _function_range(site)
    for n in range(site.line - 1, start - 1, -1):
        text = site.lines[n - 1]
        if text.strip().startswith(("if", "} else if")) and array_len.search(text) \
                and not re.search(rf"\b{re.escape(index)}\b", text):
            return [RuleCandidate(
                Location.at(site.file, n, role="missing_check"), "R-BOUNDS-PARTIAL", 0.8,
                evidence=(f"crash reads {array}[{index}] at line {site.line}",
                          f"line {n} checks {array}.length but not {index}: {text.strip()}"),
                assumptions=(f"{index} can reach {array}.length",),
            )]
    return []


def loop_off_by_one(ctx: CrashContext, site: CrashSite) -> list[RuleCandidate]:
    if not _RANGE_ERRORS.search(message(ctx)):
        return []
    start, _ = _function_range(site)
    for n in range(site.line, start - 1, -1):
        match = _LOOP_LE.search(site.lines[n - 1])
        if match:
            return [RuleCandidate(
                Location.at(site.file, n, role="off_by_one"), "R-BOUNDS-LOOP", 0.7,
                evidence=(f"{message(ctx)} at line {site.line}",
                          f"line {n} loops while {match.group(1)} <= {match.group(2).strip()}"),
                assumptions=("the last iteration indexes one element past the end",),
            )]
    return []
