"""Baseline locator: report the top business stack frame as the root cause.

This is the reference point every smarter locator must beat. It uses no LLM.
"""

from __future__ import annotations

import logging

from dfx_repair.ingest import CrashContext
from dfx_repair.retrieve import MapStatus, SourceIndex

from .result import LocateResult, Location

logger = logging.getLogger(__name__)

METHOD = "stack_top"


def locate_stack_top(case_id: str, ctx: CrashContext, index: SourceIndex, source_hash: str = "") -> LocateResult:
    """Map the top business frame to source and return it as the only candidate."""
    top = ctx.top_business_frame
    if top is None:
        return LocateResult(case_id, METHOD, None, (), assumptions=("no business frame in log",),
                            source_hash=source_hash)

    mapping = index.resolve(top.path)
    if mapping.status is MapStatus.NOT_FOUND:
        return LocateResult(case_id, METHOD, None, (),
                            evidence=(f"top frame {top.raw}",),
                            assumptions=(f"frame path not found in source: {top.path}",),
                            source_hash=source_hash)

    crash = Location.at(mapping.candidates[0], top.line, role="crash_site")
    evidence = (f"{ctx.error_name}: {ctx.error_message}", f"top frame {top.raw}")
    assumptions = ("root cause is at the crash site",)
    if mapping.status is MapStatus.MULTIPLE:
        assumptions += (f"{len(mapping.candidates)} files match {top.path}; picked the first",)
    score = 1.0 if mapping.status is MapStatus.UNIQUE else 0.5
    return LocateResult(case_id, METHOD, crash, (Location.at(crash.file, crash.start_line),),
                        evidence=evidence, assumptions=assumptions, score=score,
                        source_hash=source_hash)
