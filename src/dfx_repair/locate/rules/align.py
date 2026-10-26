"""Align a log position only when a unique nearby source construct supports it."""

from __future__ import annotations

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve.code_context import enclosing_function
from dfx_repair.retrieve.lexical import mask_non_code

from .base import CrashSite, expected_construct

MAX_OFFSET = 8


def align_crash_line(ctx: CrashContext, file: str, line: int, col: int,
                     lines: tuple[str, ...]) -> CrashSite:
    """Preserve the original log position; never clamp invalid lines or break ties.

    The error-derived construct is a heuristic, not a source map. An alignment
    remains a hypothesis, with its original position recorded in ``aligned_from``.
    """
    if not 1 <= line <= len(lines):
        return CrashSite(file, line, col, lines, valid=False,
                         warnings=("log line is outside the current source snapshot",))
    # Keep the original line slots, including a final empty source line.
    code_lines = mask_non_code("\n".join(lines)).split("\n")
    construct = expected_construct(ctx)

    def plausible(n: int) -> bool:
        text = code_lines[n - 1]
        return bool(text.strip()) and (construct is None or construct.search(text) is not None)

    if plausible(line):
        return CrashSite(file, line, col, lines)
    if construct is None:
        return CrashSite(file, line, col, lines, valid=False,
                         warnings=("no error-derived construct supports line alignment",))
    span = enclosing_function(list(lines), line)
    lower, upper = max(1, line - MAX_OFFSET), min(len(lines), line + MAX_OFFSET)
    if span:
        lower, upper = max(lower, span.start_line), min(upper, span.end_line)
    candidates = [n for n in range(lower, upper + 1) if plausible(n)]
    if len(candidates) == 1:
        # The old column belongs to a different build/line and must not select an expression.
        return CrashSite(file, candidates[0], 0, lines, aligned_from=line,
                         warnings=("line alignment is heuristic; source version remains unverified",))
    reason = "ambiguous line alignment" if candidates else "no supported source line near log position"
    return CrashSite(file, line, col, lines, valid=False, warnings=(reason,), alternatives=tuple(candidates))
