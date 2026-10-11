"""Check 5, narrative.

The LLM may only POINT at two passages that appear to contradict each other and quote both. Code checks that
both quotes exist, each in one place in the text it was given. If either cannot be found, the quotes are dropped
and the item is kept only as "unclear" (no quotes), so an officer is not shown an invented passage.
"""

from __future__ import annotations

from typing import Any

from app.llm import LLMError
from app.pipeline.consistency.context import ConsistencyContext
from app.pipeline.consistency.llm_checks import (
    SYSTEM_NARRATIVE, NarrativeOut, combined_text, locate, make_prompt, safe_topic,
)
from app.pipeline.consistency.models import Evidence, Flag, flag, make_key

TYPE = "narrative"
MAX_ITEMS = 6


def run(ctx: ConsistencyContext, trace: dict[str, Any]) -> list[Flag]:
    built = combined_text(ctx)
    if built is None:
        trace["skipped"] = "No AI available, or the text could not be sent."
        return []
    text, segments, notes = built
    trace["input"] = {"sources": [{"source": s.source, "label": s.label, "characters": len(s.text)} for s in segments], "notes": notes}
    try:
        out = ctx.guard.call_llm(
            make_prompt("narrative", SYSTEM_NARRATIVE, "Point at contradictions between statements (see instructions). Quote exactly.",
                        text, "narrative"), NarrativeOut)
    except LLMError as exc:
        trace["error"] = type(exc).__name__
        return []
    flags: list[Flag] = []
    items = []
    for c in out.contradictions[:MAX_ITEMS]:
        a_seg, a_how = locate(c.first_quote, segments, ctx.fuzzy_threshold)
        b_seg, b_how = locate(c.second_quote, segments, ctx.fuzzy_threshold)
        same_place = a_seg is not None and b_seg is not None and a_seg.source == b_seg.source and c.first_quote.strip() == c.second_quote.strip()
        verified = a_seg is not None and b_seg is not None and not same_place
        topic = safe_topic(c.topic)
        items.append({"topic": c.topic, "first_quote": c.first_quote, "second_quote": c.second_quote, "verified": verified,
                      "first_source": a_seg.source if a_seg else None, "second_source": b_seg.source if b_seg else None})
        if verified:
            flags.append(flag(
                "narrative.conflicting_statements", TYPE, "strong",
                f"Two statements about {topic} do not fit together. Read both passages.",
                [Evidence(kind="quote", source=a_seg.source, label=f"First statement ({a_seg.label})", quote=c.first_quote, verified=True),
                 Evidence(kind="quote", source=b_seg.source, label=f"Second statement ({b_seg.label})", quote=c.second_quote, verified=True)],
                key_parts=(topic, a_seg.source, b_seg.source)))
        else:
            # Quotes dropped: kept only as an unclear item with no passages.
            flags.append(flag(
                "narrative.unverified", TYPE, "weak",
                f"The AI reported a possible conflict about {topic}, but its quotes could not be found in the text, so nothing is shown. Treat as unclear.",
                [], verification="unclear", key_parts=(topic,)))
    trace["returned"] = items
    trace["verified"] = sum(i["verified"] for i in items)
    trace["dropped_unverified"] = sum(not i["verified"] for i in items)
    return flags
