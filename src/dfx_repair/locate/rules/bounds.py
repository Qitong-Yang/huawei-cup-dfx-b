"""Conservative boundary hypotheses supported by lexical scope and local dependencies.

The checks establish structural plausibility, not runtime reachability. Every rule
therefore keeps explicit assumptions and never declares a confirmed diagnosis.
"""

from __future__ import annotations

import re

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve import enclosing_function
from dfx_repair.retrieve.lexical import mask_non_code, matching_braces

from ..result import Location
from .base import CrashSite, RuleCandidate, message

_UNDEFINED_READ = re.compile(r"Cannot read property '?([\w$]+)'? of undefined", re.IGNORECASE)
_RANGE_ERRORS = re.compile(
    r"elementSize\s*>\s*viewSize|out-of-bounds|out of range|offset is outside the bounds",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*|\d+(?:\.\d+)?")
_ASSIGNMENT = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*(?::[^=;]+)?=(?!=)\s*(.*?)\s*;?\s*$"
)
_LOOP_LE = re.compile(
    r"\bfor\s*\(\s*(?:let|var|const)?\s*(?P<var>[\w$]+)\s*=\s*[^;]+;"
    r"\s*(?P=var)\s*<=\s*(?P<bound>[^;]+);[^)]*\)\s*(?P<brace>\{)"
)


def _structure(site: CrashSite):
    if not site.valid or not 1 <= site.line <= len(site.lines):
        return None
    span = enclosing_function(list(site.lines), site.line)
    if span is None:
        return None  # do not search an arbitrary preceding 40-line window
    code = mask_non_code("\n".join(site.lines))
    lines = code.split("\n")
    offsets = [0]
    for line in lines[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)
    target = offsets[site.line - 1]
    return span, code, lines, offsets, target, matching_braces(code)


def _parents(position: int, pairs: dict[int, int]) -> tuple[int, ...]:
    return tuple(sorted(start for start, end in pairs.items() if start < position < end))


def _assignments(lines: list[str], start: int, end: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for text in lines[start - 1:end]:
        match = _ASSIGNMENT.fullmatch(text)
        if match:
            values[match[1]] = match[2].rstrip(";")
    return values


def _tokens(expression: str, assignments: dict[str, str], seen: frozenset[str] = frozenset()) -> set[str]:
    tokens = set(_TOKEN.findall(expression))
    for name in list(tokens - seen):
        if name in assignments:
            tokens.update(_tokens(assignments[name], assignments, seen | {name}))
    return tokens


def partial_bounds_check(ctx: CrashContext, site: CrashSite) -> list[RuleCandidate]:
    property_match = _UNDEFINED_READ.search(message(ctx))
    if property_match is None or not (structure := _structure(site)):
        return []
    span, code, lines, offsets, target, pairs = structure
    prop = re.escape(property_match[1])
    accesses = list(re.finditer(rf"([\w$.]+)\s*\[\s*([\w$.]+)\s*\]\s*\.\s*{prop}\b",
                               lines[site.line - 1]))
    if site.col > 0:
        accesses = [m for m in accesses if m.start() < site.col <= m.end() + 1]
    if len(accesses) != 1:
        return []
    array, index = accesses[0][1], accesses[0][2]
    array_len = re.compile(rf"(?<![\w$.]){re.escape(array)}\s*\.\s*length\b")
    function_start = offsets[span.start_line - 1]
    eligible: list[tuple[int, str]] = []
    for guard in reversed(list(re.finditer(r"\bif\s*\(([^{};]+)\)\s*(\{)", code[function_start:target]))):
        opening = function_start + guard.start(2)
        closing = pairs.get(opening)
        if closing is None or closing >= target:
            continue
        condition = guard[1]
        if not array_len.search(condition):
            continue
        # A previous branch inside an unrelated block cannot dominate this read.
        if not set(_parents(opening, pairs)).issubset(_parents(target, pairs)):
            continue
        body = code[opening + 1:closing]
        if not re.fullmatch(r"\s*(?:return\b[^{}]*|throw\b[^{}]*)\s*", body):
            continue
        # Require an actual comparison to the upper bound, not logging/length truthiness.
        length = rf"{re.escape(array)}\s*\.\s*length"
        if not re.search(rf"(?:[\w$.]+\s*(?:>=|>)\s*{length}|{length}\s*(?:<=|<)\s*[\w$.]+)", condition):
            continue
        if re.search(rf"(?<![\w$]){re.escape(index)}(?![\w$])", condition):
            # Another dominating exit guard already mentions the read index. Do
            # not backtrack past it and incorrectly claim that it is unchecked.
            return []
        intervening = code[closing + 1:target]
        if re.search(rf"(?<![\w$.]){re.escape(array)}\s*=(?!=)", intervening):
            continue
        n = code.count("\n", 0, function_start + guard.start()) + 1
        eligible.append((n, condition))
    if eligible:
        n, _ = eligible[0]
        return [RuleCandidate(
            Location.at(site.file, n, role="missing_check"), "R-BOUNDS-PARTIAL", 0.75,
            evidence=(f"crash reads {array}[{index}].{property_match[1]} at line {site.line}",
                      f"line {n} has an unconditional exiting guard for another index and {array}.length"),
            assumptions=(f"runtime input must establish that {index} can reach {array}.length",
                         "exclude sparse elements, upstream mutation, and other guards before repair"),
        )]
    return []


def loop_off_by_one(ctx: CrashContext, site: CrashSite) -> list[RuleCandidate]:
    if not _RANGE_ERRORS.search(message(ctx)) or not (structure := _structure(site)):
        return []
    span, code, lines, offsets, target, pairs = structure
    reads = list(re.finditer(r"([\w$]+)\s*\.\s*get(?:Int|Uint|Float)\d+\s*\(\s*([^,;)]+)",
                             lines[site.line - 1]))
    if len(reads) != 1:
        return []
    view, offset = reads[0][1], reads[0][2]
    before = _assignments(lines, span.start_line, site.line - 1)
    view_expr = before.get(view, "")
    view_match = re.fullmatch(r"new\s+DataView\s*\(\s*([\w$.]+)\s*\)", view_expr)
    if not view_match:
        return []  # nonzero DataView offsets/lengths require separate reasoning
    buffer = view_match[1]
    dimensions = {f"{buffer}.byteLength"}
    buffer_expr = before.get(buffer, "")
    allocation = re.fullmatch(r"new\s+ArrayBuffer\s*\(\s*(.+?)\s*\)", buffer_expr)
    if allocation:
        dimensions.update(_tokens(allocation[1], before))
    function_start = offsets[span.start_line - 1]
    for loop in reversed(list(_LOOP_LE.finditer(code[function_start:target]))):
        opening = function_start + loop.start("brace")
        closing = pairs.get(opening)
        if closing is None or not opening < target < closing:
            continue
        var, bound = loop["var"], loop["bound"].strip()
        body_before_read = code[opening + 1:target]
        if re.search(rf"\b{re.escape(var)}\s*(?:=(?!=)|\+\+|--)", body_before_read):
            continue
        if var not in _tokens(offset, before):
            continue
        bound_expr = before.get(bound, bound)
        if not dimensions.intersection(_tokens(bound_expr, before)):
            continue
        # The bound should describe a count, and the read offset must use its stride.
        quotient = re.fullmatch(r"\s*(.+?)\s*/\s*([\w$.]+|\d+(?:\.\d+)?)\s*", bound_expr)
        if not quotient or quotient[2] not in _tokens(offset, before):
            continue
        n = code.count("\n", 0, function_start + loop.start()) + 1
        return [RuleCandidate(
            Location.at(site.file, n, role="off_by_one"), "R-BOUNDS-LOOP", 0.7,
            evidence=(f"{message(ctx)} at line {site.line}",
                      f"line {n} encloses the read and uses {var} <= {bound}",
                      f"{view} reads the buffer with an offset depending on {var} and the count stride"),
            assumptions=("runtime must confirm a terminal iteration reaches the exclusive buffer extent",
                         "verify buffer alignment, positive step, channel layout, and control-flow paths"),
        )]
    return []
