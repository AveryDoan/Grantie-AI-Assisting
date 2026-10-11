"""Check 2, timeline.

The LLM only EXTRACTS dated events, each with an exact quote. Code verifies every quote, then checks the
order, overlaps and ages. Items whose quote cannot be found are dropped (and counted in the trace).
Two further checks use fields read by code from the documents (a visa granted before the CoE existed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.llm import LLMError
from app.pipeline.consistency.common import line_matching, months_between, parse_when, years_between
from app.pipeline.consistency.context import ConsistencyContext
from app.pipeline.consistency.llm_checks import (
    SYSTEM_TIMELINE, Segment, TimelineOut, combined_text, locate, make_prompt,
)
from app.pipeline.consistency.models import Evidence, Flag, flag
from app.pipeline.parsing import parse_date

TYPE = "timeline"
MIN_AGE_FOR_ROLE = 14
MIN_OVERLAP_MONTHS = 3


@dataclass
class Event:
    label: str
    kind: str
    start: date | None
    end: date | None
    full_time: bool | None
    quote: str
    source: str
    segment_label: str


def _ev(e: Event) -> Evidence:
    return Evidence(kind="quote", source=e.source, label=f"{e.label} ({e.segment_label})", quote=e.quote, verified=True)


def extract_events(ctx: ConsistencyContext, trace: dict[str, Any]) -> list[Event]:
    """Ask the AI for dated events; keep only those whose quote the code finds in the text."""
    built = combined_text(ctx)
    if built is None:
        trace["skipped"] = "No AI available, or the text could not be sent."
        return []
    text, segments, notes = built
    trace["input"] = {"sources": [{"source": s.source, "label": s.label, "characters": len(s.text)} for s in segments], "notes": notes}
    try:
        out = ctx.guard.call_llm(
            make_prompt("timeline", SYSTEM_TIMELINE, "List the dated events (see instructions). Quote exactly.", text, "timeline"),
            TimelineOut)
    except LLMError as exc:
        trace["error"] = type(exc).__name__
        return []
    events: list[Event] = []
    items = []
    for item in out.events[:40]:
        seg, method = locate(item.quote, segments, ctx.fuzzy_threshold)
        start = parse_when(item.start)
        end = ctx.submitted if (item.end or "").strip().lower() in ("present", "current", "now") else parse_when(item.end, end=True)
        kind = item.kind if item.kind in ("role", "study", "other") else "other"
        items.append({"label": item.label, "kind": kind, "start": item.start, "end": item.end, "full_time": item.full_time,
                      "quote": item.quote, "verified": seg is not None, "method": method,
                      "source": seg.source if seg else None})
        if seg is None:
            continue   # an unverified quote is dropped
        if not (start or end):
            continue
        events.append(Event(item.label[:80], kind, start, end, item.full_time, item.quote, seg.source, seg.label))
    trace["returned"] = items
    trace["verified"] = sum(i["verified"] for i in items)
    trace["dropped_unverified"] = sum(not i["verified"] for i in items)
    return events


def check_events(ctx: ConsistencyContext, events: list[Event]) -> list[Flag]:
    out: list[Flag] = []
    dob = parse_date(ctx.fields.get("date_of_birth"))
    for e in events:
        if e.start and e.end and e.end < e.start:
            out.append(flag("timeline.dates_backwards", TYPE, "strong",
                            f"The dates for '{e.label}' run backwards: it ends before it starts.", [_ev(e)], key_parts=(e.label,)))
        if dob and e.start and e.kind == "role":
            age = years_between(dob, e.start)
            if age < MIN_AGE_FOR_ROLE:
                out.append(flag("timeline.role_before_age", TYPE, "strong",
                                f"'{e.label}' is described as starting when the applicant would have been about {max(age, 0):.0f} years old.",
                                [_ev(e), Evidence(kind="field", source="form", label="Date of birth on the form (not shown)", field="date_of_birth")],
                                key_parts=(e.label, str(e.start))))
    full = [e for e in events if e.full_time and e.start and e.kind in ("role", "study")]
    for i, a in enumerate(full):
        for b in full[i + 1:]:
            if a.kind == "study" and b.kind == "study":
                continue
            lo, hi = max(a.start, b.start), min(a.end or ctx.submitted, b.end or ctx.submitted)
            if hi > lo and months_between(lo, hi) >= MIN_OVERLAP_MONTHS:
                what = "a full-time job and full-time study" if {a.kind, b.kind} == {"role", "study"} else "two full-time roles"
                out.append(flag("timeline.overlapping_full_time", TYPE, "strong",
                                f"{what[0].upper()}{what[1:]} are described over the same {months_between(lo, hi):.0f} months.",
                                [_ev(a), _ev(b)], key_parts=tuple(sorted((a.label, b.label)))))
    return out


def visa_before_coe(ctx: ConsistencyContext) -> list[Flag]:
    """A visa cannot be granted on a CoE that did not exist yet (both dates read by code)."""
    visa, coe = ctx.first("visa"), ctx.first("coe")
    if not (visa and coe):
        return []
    granted, issued = visa.fields.get("visa_grant_date_iso"), coe.fields.get("coe_issue_date_iso")
    if not (granted and issued) or date.fromisoformat(granted) >= date.fromisoformat(issued):
        return []
    ev = [Evidence(kind="quote", source=d.source, label=f"{lab} ({d.label})", quote=line, verified=line in d.text)
          for d, lab, line in ((visa, "Visa grant date", line_matching(visa.text, r"date of grant|grant date")),
                               (coe, "CoE issue date", line_matching(coe.text, r"date coe issued|coe issue date"))) if line]
    days = (date.fromisoformat(issued) - date.fromisoformat(granted)).days
    return [flag("timeline.visa_before_coe", TYPE, "strong",
                 f"The visa is dated {days} days before the Confirmation of Enrolment was issued.", ev, key_parts=(granted, issued))]


def run(ctx: ConsistencyContext, trace: dict[str, Any]) -> list[Flag]:
    flags = visa_before_coe(ctx)
    events = extract_events(ctx, trace)
    flags += check_events(ctx, events)
    return flags
