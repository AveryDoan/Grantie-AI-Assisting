"""Check 1, cross_document (plain code): do the form, the CoE, the arrival evidence and the
referee letters agree with each other?

Every flag quotes the exact lines it compares (from the redacted text) or names the form fields.
Each check can only point at a disagreement; none decides what it means.
"""

from __future__ import annotations

import re
from datetime import date

from app.pipeline.consistency.common import (
    WHEN_RE, line_matching, months_between, parse_duration_months, parse_when, sentence_matching,
)
from app.pipeline.consistency.context import ConsistencyContext, DocView
from app.pipeline.consistency.models import Evidence, Flag, flag
from app.pipeline.parsing import date_candidates, parse_date

TYPE = "cross_document"
WINDOW = (date(2024, 1, 1), date(2026, 12, 31))   # letters should be dated inside this window (rule D5)
LATE_ARRIVAL_DAYS = 14                            # up to two weeks after the start can be agreed with a provider
MAX_EARLY_ARRIVAL_DAYS = 120                       # arriving more than ~4 months before the course starts
LENGTH_TOLERANCE_MONTHS = 6


def _quote(doc: DocView, line: str | None, label: str) -> Evidence | None:
    if not line:
        return None
    return Evidence(kind="quote", source=doc.source, label=f"{label} ({doc.label})", quote=line, verified=line in doc.text)


def _field(name: str, value: str | None, label: str) -> Evidence:
    return Evidence(kind="field", source="form", label=label, field=name, value=value)


def _date(doc: DocView | None, key: str) -> date | None:
    iso = (doc.fields.get(f"{key}_iso") if doc else None)
    return date.fromisoformat(iso) if iso else None


def _typed(ctx: ConsistencyContext, key: str) -> date | None:
    return parse_date(ctx.fields.get(key))


# ---------------------------------------------------------------------------


def arrival_vs_start(ctx: ConsistencyContext) -> list[Flag]:
    """Arrival must come before the course starts, by a plausible gap."""
    coe, booking = ctx.first("coe"), ctx.first("travel_booking")
    start = _date(coe, "course_start_date") or _typed(ctx, "course_start_date")
    arrival = _date(booking, "arrival_date") or _typed(ctx, "arrival_date")
    if not (start and arrival):
        return []
    evidence: list[Evidence] = []
    if coe and _date(coe, "course_start_date"):
        evidence.append(_quote(coe, line_matching(coe.text, r"course start date|start date"), "Course start date"))
    else:
        evidence.append(_field("course_start_date", ctx.fields.get("course_start_date"), "Course start date typed on the form"))
    if booking and _date(booking, "arrival_date"):
        evidence.append(_quote(booking, line_matching(booking.text, r"arriv"), "Arrival date"))
    else:
        evidence.append(_field("arrival_date", ctx.fields.get("arrival_date"), "Arrival date typed on the form"))
    evidence = [e for e in evidence if e]
    gap = (start - arrival).days
    if gap < 0:
        # A few days late can be agreed with the provider (weak); weeks late is a real mismatch (strong).
        strength = "strong" if -gap > LATE_ARRIVAL_DAYS else "weak"
        return [flag("cross_document.arrival_vs_start", TYPE, strength,
                     f"The arrival date is {-gap} days after the course starts. Arrival evidence usually comes before the start date"
                     + (", although a short late arrival can be agreed with the provider." if strength == "weak" else "."),
                     evidence, key_parts=(str(gap),))]
    if gap > MAX_EARLY_ARRIVAL_DAYS:
        return [flag("cross_document.arrival_vs_start", TYPE, "weak",
                     f"The arrival date is {gap} days before the course starts, which is much earlier than usual.",
                     evidence, key_parts=(str(gap),))]
    return []


def typed_vs_documents(ctx: ConsistencyContext) -> list[Flag]:
    """What the applicant typed against what the documents show."""
    from rapidfuzz import fuzz

    out: list[Flag] = []
    coe, booking = ctx.first("coe"), ctx.first("travel_booking")
    # (typed field, document, document field, label, quote pattern, tag)
    dates = [
        ("course_start_date", coe, "course_start_date", "Course start date", r"course start date|start date", "coe_start"),
        ("course_end_date", coe, "course_end_date", "Course end date", r"course end date|end date", "coe_end"),
        ("arrival_date", booking, "arrival_date", "Arrival date", r"arriv", "arrival"),
    ]
    for typed_key, doc, doc_key, label, pattern, tag in dates:
        typed_text, doc_date = ctx.fields.get(typed_key), _date(doc, doc_key)
        if not (typed_text and doc and doc_date):
            continue
        typed_dates = date_candidates(typed_text)
        if not typed_dates or doc_date in typed_dates or min(abs((t - doc_date).days) for t in typed_dates) <= 2:
            continue
        ev = [_field(typed_key, typed_text, f"{label} typed on the form")]
        if q := _quote(doc, line_matching(doc.text, pattern), label):
            ev.append(q)
        out.append(flag(f"cross_document.typed_{tag}", TYPE, "strong",
                        f"The {label.lower()} on the form does not match the {doc.label.lower()}.", ev,
                        key_parts=(tag, str(doc_date))))

    provider, coe_provider = ctx.fields.get("education_provider"), (coe.fields.get("provider_name") if coe else None)
    if provider and coe_provider and fuzz.token_set_ratio(provider.lower(), coe_provider.lower()) < 80:
        ev = [_field("education_provider", provider, "Provider typed on the form")]
        if q := _quote(coe, line_matching(coe.text, re.escape(coe_provider)), "Provider"):
            ev.append(q)
        out.append(flag("cross_document.provider", TYPE, "strong",
                        "The education provider on the form is different from the provider on the Confirmation of Enrolment.",
                        ev, key_parts=("provider",)))

    course, coe_course = ctx.fields.get("course_name"), (coe.fields.get("course_name") if coe else None)
    if course and coe_course and fuzz.token_set_ratio(course.lower(), coe_course.lower()) < 70:
        ev = [_field("course_name", course, "Course typed on the form")]
        if q := _quote(coe, line_matching(coe.text, re.escape(coe_course)), "Course"):
            ev.append(q)
        out.append(flag("cross_document.course", TYPE, "strong",
                        "The course on the form is different from the course on the Confirmation of Enrolment.", ev,
                        key_parts=("course",)))
    return out


def coe_length(ctx: ConsistencyContext) -> list[Flag]:
    """The CoE's start and end dates should fit the length it states."""
    coe = ctx.first("coe")
    if not coe:
        return []
    start, end = _date(coe, "course_start_date"), _date(coe, "course_end_date")
    stated = parse_duration_months(coe.fields.get("course_duration"))
    if not (start and end and stated):
        return []
    span = months_between(start, end)
    if abs(span - stated) <= 4:
        return []
    ev = [e for e in (
        _quote(coe, line_matching(coe.text, r"course duration"), "Stated course length"),
        _quote(coe, line_matching(coe.text, r"course start date"), "Start date"),
        _quote(coe, line_matching(coe.text, r"course end date"), "End date"),
    ) if e]
    return [flag("cross_document.coe_length", TYPE, "strong",
                 f"The start and end dates on the Confirmation of Enrolment cover about {span / 12:.1f} years, "
                 f"but the course length it states is about {stated / 12:.1f} years.", ev,
                 key_parts=(f"{span:.0f}", f"{stated:.0f}"))]


def referee_dates(ctx: ConsistencyContext) -> list[Flag]:
    """Each referee letter should be dated inside 2024 to 2026, and not after the application."""
    out: list[Flag] = []
    for doc in ctx.letters():
        line = line_matching(doc.text, r"^\s*date\s*[:\-]") or sentence_matching(doc.text, WHEN_RE)
        d = None
        if line:
            m = re.search(WHEN_RE, line, flags=re.I)
            d = parse_when(m.group(0)) if m else None
        if not d:
            continue
        ev = [e for e in [_quote(doc, line, "Date on the letter")] if e]
        if d < WINDOW[0] or d > WINDOW[1]:
            out.append(flag("cross_document.referee_date_window", TYPE, "strong",
                            f"A referee letter is dated {d:%B %Y}, outside the 2024 to 2026 window the form asks for.", ev,
                            key_parts=(doc.id,)))
        elif d > ctx.submitted:
            out.append(flag("cross_document.referee_date_future", TYPE, "strong",
                            "A referee letter is dated after the application was submitted.", ev, key_parts=(doc.id,)))
        elif months_between(d, ctx.submitted) > 24:
            out.append(flag("cross_document.referee_date_old", TYPE, "weak",
                            "A referee letter is more than 24 months older than the application.", ev, key_parts=(doc.id,)))
    return out


_SINCE = re.compile(rf"(?:since|from|beginning in|starting in|started in|joined [^.]{{0,60}}? in|began [^.]{{0,60}}? in|met [^.]{{0,40}}? in)\s+({WHEN_RE})", re.I)


def known_for_vs_timeline(ctx: ConsistencyContext) -> list[Flag]:
    """'Known for five years' against the letter's own dates ('since July 2025')."""
    out: list[Flag] = []
    for doc in ctx.letters():
        letter_line = line_matching(doc.text, r"^\s*date\s*[:\-]") or ""
        m = re.search(WHEN_RE, letter_line, flags=re.I)
        written = parse_when(m.group(0)) if m else None
        stated_sentence = (sentence_matching(doc.text, r"known[^.]{0,60}\bfor\b[^.]{0,30}(?:year|month)")
                           or line_matching(doc.text, r"(?:length of association|known applicant for)\s*:"))
        stated = parse_duration_months(stated_sentence) if stated_sentence else None
        if stated is None:
            ref = ctx.referee_for(doc.id)
            if ref is not None and ref.stated("length_of_association"):
                stated_sentence = ref.fields["length_of_association"].source_quote
                stated = parse_duration_months(ref.fields["length_of_association"].fact_value)
        if not (stated and written):
            continue
        timeline_sentence = None
        implied = None
        for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\n\s*", " ", doc.text)):
            if s == stated_sentence:
                continue
            mm = _SINCE.search(s)
            when = parse_when(mm.group(1)) if mm else None
            if when and when <= written:
                months = months_between(when, written)
                if implied is None or months > implied:   # the earliest start the letter mentions
                    implied, timeline_sentence = months, s.strip()
        if implied is None:
            continue
        if abs(stated - implied) > max(LENGTH_TOLERANCE_MONTHS, 0.35 * stated):
            ev = [e for e in (_quote(doc, stated_sentence, "How long the referee says they have known the applicant"),
                              _quote(doc, timeline_sentence, "The letter's own dates")) if e]
            out.append(flag("cross_document.known_for_vs_timeline", TYPE, "strong",
                            f"A referee says they have known the applicant for about {stated / 12:.1f} years, but the dates in "
                            f"the same letter cover about {implied / 12:.1f} years.", ev, key_parts=(doc.id,)))
    return out


def letter_subject(ctx: ConsistencyContext) -> list[Flag]:
    """A letter should be about the applicant: the name it is addressed to must be the applicant's placeholder."""
    if not ctx.applicant_token:
        return []
    out: list[Flag] = []
    for doc in ctx.letters():
        line = line_matching(doc.text, r"(?:letter of (?:support|reference)|re:)[^\n]{0,60}\bfor\s+\[[A-Z]+_\d+\]")
        m = re.search(r"\bfor\s+(\[[A-Z]+_\d+\])", line or "")
        if m and m.group(1) != ctx.applicant_token and m.group(1).startswith("[PERSON_"):
            ev = [e for e in [_quote(doc, line, "Who the letter is about")] if e]
            out.append(flag("cross_document.letter_subject", TYPE, "strong",
                            "A referee letter is written about a different name from the one on the form.", ev, key_parts=(doc.id,)))
    return out


def run(ctx: ConsistencyContext) -> list[Flag]:
    flags: list[Flag] = []
    for fn in (arrival_vs_start, typed_vs_documents, coe_length, referee_dates, known_for_vs_timeline, letter_subject):
        flags.extend(fn(ctx))
    return flags
