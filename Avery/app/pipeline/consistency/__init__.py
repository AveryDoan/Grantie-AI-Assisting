"""Consistency layer: signals that details may not match across an application.

Signals, never verdicts. Each check can only point at a disagreement and show the evidence; an officer confirms or
dismisses every flag, a flag never changes a rule result, and nothing here is a score, rating or recommendation.

    cross_document     plain code     form, CoE, arrival evidence and referee letters against each other
    timeline           AI + code      the AI extracts dated events with quotes; code checks order, overlap, age
    document_integrity plain code     PDF metadata and missing marks: ALWAYS weak
    cross_application  plain code     shared contacts, referees or wording across the pool (keyed hashes only)
    narrative          AI + code      statements that contradict each other, with both quotes verified by code

`run_consistency` runs the checks for ONE application. cross_application needs the whole pool, so the service
layer runs it from the stored hashes (see app.services.consistency).
"""

from __future__ import annotations

from typing import Any

from app.pipeline.consistency import cross_document, document_integrity, narrative, timeline
from app.pipeline.consistency.context import ConsistencyContext
from app.pipeline.consistency.models import CHECK_TYPES, ConsistencyResult, Flag
from app.logging_utils import get_logger

log = get_logger(__name__)
__all__ = ["run_consistency", "ConsistencyContext", "ConsistencyResult", "Flag", "CHECK_TYPES"]


def run_consistency(ctx: ConsistencyContext) -> ConsistencyResult:
    """Run the per-application checks. A failing check never stops the others or the assessment."""
    flags: list[Flag] = []
    trace: dict[str, Any] = {}

    for name, fn in (("cross_document", cross_document.run), ("document_integrity", document_integrity.run)):
        t: dict[str, Any] = {}
        try:
            got = fn(ctx)
            flags.extend(got)
            t["flags"] = len(got)
        except Exception as exc:  # a check must never break an assessment; only the error TYPE is recorded
            log.warning("consistency check %s failed: %s", name, type(exc).__name__)
            t["error"] = type(exc).__name__
        trace[name] = t
    t_timeline: dict[str, Any] = {}
    t_narrative: dict[str, Any] = {}
    for name, mod, t in (("timeline", timeline, t_timeline), ("narrative", narrative, t_narrative)):
        try:
            got = mod.run(ctx, t)
            flags.extend(got)
            t["flags"] = len(got)
        except Exception as exc:
            log.warning("consistency check %s failed: %s", name, type(exc).__name__)
            t["error"] = type(exc).__name__
        trace[name] = t
    unique: dict[str, Flag] = {}
    for f in flags:
        unique.setdefault(f.key, f)
    return ConsistencyResult(flags=list(unique.values()), trace=trace)
