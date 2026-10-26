"""Baseline: nominate the top business frame, without claiming it proves root cause."""

from __future__ import annotations

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve import MapStatus, SourceIndex

from .result import LocateResult, Location
from .rules.base import read_lines

METHOD = "stack_top"


def locate_stack_top(case_id: str, ctx: CrashContext, index: SourceIndex,
                     source_hash: str = "") -> LocateResult:
    """Return no primary answer when file identity or source coordinates are unresolved."""
    top = ctx.top_business_frame
    evidence: tuple[str, ...] = (f"{ctx.error_name}: {ctx.error_message}",)
    if top is None:
        return LocateResult(case_id, METHOD, None, (), evidence=evidence,
                            assumptions=("no business frame in log",), source_hash=source_hash,
                            status="UNRESOLVED", context_requests=("obtain a business stack frame",))
    evidence += (f"top frame {top.raw}",)
    mapping = index.resolve(top.path)
    if mapping.status is not MapStatus.UNIQUE:
        detail = f"{mapping.status.value}: {top.path}; candidates={mapping.candidates}"
        return LocateResult(case_id, METHOD, None, (), evidence=evidence,
                            assumptions=(detail,), source_hash=source_hash, status="UNRESOLVED",
                            context_requests=("disambiguate source using module/build metadata",),
                            unresolved_candidates=tuple(Location.at(f, top.line, role="crash_site_candidate")
                                                        for f in mapping.candidates))
    file = mapping.candidates[0]
    lines = read_lines(str(index.app_root), file)
    if not 1 <= top.line <= len(lines):
        return LocateResult(case_id, METHOD, None, (), evidence=evidence,
                            assumptions=("log line is outside the current source snapshot",),
                            source_hash=source_hash, status="UNRESOLVED",
                            context_requests=("obtain source matching the crashed build",))
    crash = Location.at(file, top.line, role="crash_site")
    return LocateResult(case_id, METHOD, crash, (Location.at(file, top.line),),
                        evidence=evidence, assumptions=("root cause is assumed at the crash site",),
                        score=0.5, source_hash=source_hash, status="CANDIDATE",
                        context_requests=("trace inputs to establish the actual root cause",))
