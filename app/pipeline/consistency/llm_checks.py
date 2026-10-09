"""Shared plumbing for the two AI-assisted checks (timeline and narrative).

The LLM only ever sees REDACTED text, through GuardedLLM. It may only point at passages: every quote it
returns is checked by code against the text it was given, and an item whose quote cannot be found is dropped.
The LLM never judges intent, honesty or credibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.llm.base import Prompt
from app.pipeline.consistency.context import ConsistencyContext
from app.pipeline.injection import neutralise_delimiters
from app.pipeline.prompts import _DATA_RULES, PROMPT_VERSION
from app.pipeline.verification import verify_quote

MAX_PART_CHARS = 30000
CONSISTENCY_PROMPT_VERSION = f"{PROMPT_VERSION}-cx1"


@dataclass
class Segment:
    source: str      # "application_text" or "document:<id>"
    label: str
    start: int
    end: int
    text: str


def combined_text(ctx: ConsistencyContext) -> tuple[str, list[Segment], list[str]] | None:
    """(combined redacted text, segments, notes) or None when the AI cannot be used."""
    if ctx.guard is None or not ctx.form_text:
        return None
    parts = [("application form", ctx.form_text, "application_text")]
    notes: list[str] = []
    for d in ctx.docs:
        if not d.text:
            continue
        if len(d.text) > MAX_PART_CHARS:
            notes.append(f"{d.label} is too long to include")
            continue
        parts.append((d.label, d.text, d.source))
    text, segs = ctx.guard.combine([(label, t) for label, t, _ in parts])
    sources = [src for _, _, src in parts]
    return text, [Segment(src, label, a, b, t) for (label, a, b, t), src in zip(segs, sources)], notes


def locate(quote: str | None, segments: list[Segment], threshold: float) -> tuple[Segment | None, str]:
    """The ONE segment in which `quote` is found by code, and how (exact | normalised | fuzzy)."""
    if not quote or not quote.strip():
        return None, "none"
    for seg in segments:
        qc = verify_quote(quote, seg.text, threshold=threshold)
        if qc.verified:
            return seg, qc.method
    return None, "none"


def make_prompt(task: str, system: str, instruction: str, text: str, kind: str, rule_pack_version: str = "consistency") -> Prompt:
    user = f"{instruction}\n\n<application_data>\n{neutralise_delimiters(text)}\n</application_data>"
    return Prompt(task=task, system=system, user=user, prompt_version=CONSISTENCY_PROMPT_VERSION, cache_text=text,
                  rule_pack_version=rule_pack_version, meta={"kind": kind, "source": text})


_RULES = f"""\
{_DATA_RULES}

YOU ONLY POINT AT TEXT. You never judge intent, honesty, character or credibility, and you never say that
anything is false, forged or deceptive. A human officer decides what, if anything, a difference means.
Every quote must be an exact, contiguous, word-for-word copy from ONE place in the data. Never paraphrase."""

SYSTEM_TIMELINE = f"""\
You help a grants officer by listing the dated events in an application (jobs, roles, volunteering, study,
enrolment) so that plain code can check the order of the dates.

{_RULES}

For each event give: a short neutral label; kind ("role" for jobs, volunteering and leadership roles, "study"
for studying, "other" otherwise); start and end normalised to "YYYY-MM" or "YYYY" using only what the text
states (use "present" for an end that the text says is current); full_time true or false if the text says so,
else null; and an exact quote containing the dates. List only events whose dates are written in the text.
Never guess, infer or calculate dates. Return only the JSON object."""

SYSTEM_NARRATIVE = f"""\
You help a grants officer by pointing at statements in an application that CONTRADICT each other, for
example one place saying three years of study where another place shows one year.

{_RULES}

For each contradiction give the two quotes and a short neutral topic (a noun phrase such as "years of study").
Report only direct contradictions about facts (numbers, dates, durations, places, roles). Do not report
differences in style, spelling, grammar, polish or level of detail, and do not report missing information.
If you are not sure it is a direct contradiction, leave it out. If there are none, return an empty list.
Return only the JSON object."""


class TimelineEvent(BaseModel):
    label: str = Field(description="Short neutral label, e.g. 'Software developer, Example Pty Ltd'")
    kind: str = Field(description='"role", "study" or "other"')
    start: str | None = Field(description='"YYYY-MM" or "YYYY", or null if not stated')
    end: str | None = Field(description='"YYYY-MM", "YYYY" or "present", or null if not stated')
    full_time: bool | None = Field(description="true or false only if the text says so, else null")
    quote: str = Field(description="Exact verbatim words containing the dates")


class TimelineOut(BaseModel):
    events: list[TimelineEvent]


class Contradiction(BaseModel):
    topic: str = Field(description="Short neutral noun phrase, e.g. 'years of study'")
    first_quote: str = Field(description="Exact verbatim words from one place")
    second_quote: str = Field(description="Exact verbatim words from another place")


class NarrativeOut(BaseModel):
    contradictions: list[Contradiction]


def safe_topic(topic: str) -> str:
    """A short, neutral topic for a description (the LLM's words are never copied into a flag unchecked)."""
    from app.pipeline.consistency.models import FORBIDDEN

    t = " ".join(topic.split())[:60]
    return t if t and not FORBIDDEN.search(t) and all(c.isalnum() or c in " -/'," for c in t) else "a detail"
