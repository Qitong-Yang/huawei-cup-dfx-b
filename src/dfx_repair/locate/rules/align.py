"""Align the logged crash line with the shipped source.

Logs can be captured from a slightly different build than the source we are given
(observed offset of 4 lines in MoneyTrack1_2). If the logged line cannot contain the
construct the error message implies (blank, comment, or no match), search nearby lines.
"""

from __future__ import annotations

from dfx_repair.ingest import CrashContext

from .base import CrashSite, expected_construct, is_code_line

MAX_OFFSET = 8


def align_crash_line(ctx: CrashContext, file: str, line: int, col: int,
                     lines: tuple[str, ...]) -> CrashSite:
    """Return the crash site, moved to the nearest matching line if the logged one is implausible."""
    construct = expected_construct(ctx)
    if not 1 <= line <= len(lines):
        return CrashSite(file, min(max(line, 1), len(lines)), col, lines, aligned_from=line)

    def plausible(n: int) -> bool:
        text = lines[n - 1]
        if not is_code_line(text):
            return False
        return construct.search(text) is not None if construct else True

    if plausible(line):
        return CrashSite(file, line, col, lines)
    for offset in range(1, MAX_OFFSET + 1):
        for n in (line + offset, line - offset):  # logs observed to lag behind the source
            if 1 <= n <= len(lines) and plausible(n):
                return CrashSite(file, n, col, lines, aligned_from=line)
    return CrashSite(file, line, col, lines)
