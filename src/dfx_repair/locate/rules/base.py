"""Shared types and helpers for rule-based localization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from dfx_repair.ingest import CrashContext

from ..result import Location


@dataclass(frozen=True)
class RuleCandidate:
    """A root-cause candidate proposed by one rule, with the evidence behind it."""

    location: Location
    rule_id: str
    score: float  # heuristic, not a probability
    evidence: tuple[str, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CrashSite:
    """Crash location after line alignment, plus the source it points into."""

    file: str
    line: int
    col: int
    lines: tuple[str, ...]  # whole file, 0-based list
    aligned_from: int | None = None  # original log line when alignment moved it
    valid: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)
    alternatives: tuple[int, ...] = ()  # equally plausible lines when alignment is ambiguous

    @property
    def text(self) -> str:
        return self.lines[self.line - 1] if self.valid and 1 <= self.line <= len(self.lines) else ""


def read_lines(app_root: str, rel: str) -> tuple[str, ...]:
    """Read the current snapshot; never cache mutable source text by pathname alone."""
    root = Path(app_root).resolve(strict=True)
    target = (root / rel).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"source path is outside the app or not a file: {rel}")
    return tuple(target.read_text(encoding="utf-8-sig", errors="strict").splitlines())


def is_code_line(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(("//", "/*", "*"))


def message(ctx: CrashContext) -> str:
    return f"{ctx.error_name}: {ctx.error_message}"


# Error message -> source construct that must appear on the crashing line.
MESSAGE_CONSTRUCTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Cannot convert a UNDEFINED value to a JSObject", re.IGNORECASE),
     r"Object\.(keys|values|entries|assign|getOwnPropertyNames)\s*\("),
    # Serialization failures can mention JSON too; do not classify them as parse errors.
    (re.compile(r"circular structure", re.IGNORECASE), r"JSON\.stringify\s*\("),
    (re.compile(r"Unexpected\b.*\bJSON|JSON(?:\.parse|\s+pars(?:e|ing))\b|(?:invalid|malformed)\s+JSON",
                re.IGNORECASE), r"JSON\.parse\s*\("),
    (re.compile(r"Syntax error|nothing to repeat|invalid class range|capture syntax|GroupName", re.IGNORECASE),
     r"RegExp\s*\(|/.+/[gimsuy]*"),
    (re.compile(r"byte length of \w+Array|newByteLength|elementSize > viewSize", re.IGNORECASE),
     r"new\s+\w*Array\s*\(|DataView|\.get(Int|Uint|Float)\d+\s*\("),
    (re.compile(r"Invalid array length", re.IGNORECASE), r"new\s+Array\b|\.length\s*="),
    (re.compile(r"cannot be (invoked|called) without 'new'|is not callable", re.IGNORECASE), r"\w\s*\("),
)


def expected_construct(ctx: CrashContext) -> re.Pattern[str] | None:
    """Regex for what the crashing line should contain, derived from the error message."""
    msg = message(ctx)
    prop = re.search(r"Cannot (?:read|set) property '?([\w$]+)'? of (?:undefined|null)", msg)
    if prop:
        return re.compile(rf"\.\s*{re.escape(prop.group(1))}\b")
    for trigger, construct in MESSAGE_CONSTRUCTS:
        if trigger.search(msg):
            return re.compile(construct)
    return None
