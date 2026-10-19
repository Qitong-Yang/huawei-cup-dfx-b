"""Rule-based localization: align the crash line, run rules, rank candidates."""

from __future__ import annotations

import logging

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve import MapStatus, SourceIndex

from ..result import LocateResult, Location
from .align import align_crash_line
from .base import CrashSite, RuleCandidate, read_lines
from .bounds import loop_off_by_one, partial_bounds_check

logger = logging.getLogger(__name__)

METHOD = "rules"
RULES = (partial_bounds_check, loop_off_by_one)

__all__ = ["CrashSite", "RuleCandidate", "locate_with_rules"]


def locate_with_rules(case_id: str, ctx: CrashContext, index: SourceIndex,
                      source_hash: str = "") -> LocateResult:
    """Rule candidates first (by score), then the aligned crash line as the fallback."""
    top = ctx.top_business_frame
    if top is None:
        return LocateResult(case_id, METHOD, None, (), assumptions=("no business frame in log",),
                            source_hash=source_hash)
    mapping = index.resolve(top.path)
    if mapping.status is MapStatus.NOT_FOUND:
        return LocateResult(case_id, METHOD, None, (), evidence=(f"top frame {top.raw}",),
                            assumptions=(f"frame path not found: {top.path}",), source_hash=source_hash)

    file = mapping.candidates[0]
    site = align_crash_line(ctx, file, top.line, top.col, read_lines(str(index.app_root), file))
    evidence = [f"{ctx.error_name}: {ctx.error_message}", f"top frame {top.raw}"]
    assumptions: list[str] = []
    if mapping.status is MapStatus.MULTIPLE:
        assumptions.append(f"{len(mapping.candidates)} files match {top.path}; picked the first")
    if site.aligned_from is not None:
        evidence.append(f"log line {site.aligned_from} realigned to {site.line}: {site.text.strip()}")

    candidates: list[RuleCandidate] = []
    for rule in RULES:
        candidates.extend(rule(ctx, site))
    candidates.sort(key=lambda c: c.score, reverse=True)

    crash = Location.at(file, site.line, role="crash_site")
    locations = [c.location for c in candidates] + [Location.at(file, site.line)]
    for c in candidates:
        evidence.extend(f"[{c.rule_id}] {e}" for e in c.evidence)
        assumptions.extend(f"[{c.rule_id}] {a}" for a in c.assumptions)
    if not candidates:
        assumptions.append("no rule matched; root cause assumed at the crash site")
    score = candidates[0].score if candidates else 0.5
    return LocateResult(case_id, METHOD, crash, tuple(locations), tuple(evidence), tuple(assumptions),
                        score, source_hash)
