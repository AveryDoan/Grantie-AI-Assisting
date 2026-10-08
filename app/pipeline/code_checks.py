"""Deterministic rule checks (check_method = code). Principle 1.

Each check is selected by `rule.params["check"]`. Dates, numbers, caps and
counts are decided here, never by the LLM.

Conventions:
- missing evidence -> "Needs evidence" (never "Not met");
- an unfilled placeholder parameter (e.g. "[closing date]") -> "Unclear"
  with error_flag, so a configuration gap never looks like a pass;
- an unverified or failed fact -> "Unclear";
- typed/document mismatch -> "Needs evidence" + clarification, never fraud.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rapidfuzz import fuzz

from app.domain import NOT_STATED, Finding, Rule
from app.pipeline.documents import DocumentChecks
from app.pipeline.facts import FactSet
from app.pipeline.parsing import parse_amount, parse_date
from app.pipeline.rules_loader import is_placeholder

DOC_LABEL = {"coe": "Confirmation of Enrolment", "visa": "visa grant notice", "travel_document": "passport or travel document"}


@dataclass
class CodeContext:
    rule: Rule
    facts: FactSet
    documents: DocumentChecks
    register_rows: list[dict[str, Any]] = field(default_factory=list)
    grant_program_id: str | None = None


def _f(ctx: CodeContext, status: str, rationale: str, **kw: Any) -> Finding:
    return Finding(
        rule_id=ctx.rule.id,
        rule_code=ctx.rule.rule_code,
        ai_status=status,  # type: ignore[arg-type]
        rationale=rationale,
        check_source="code",
        confidence=kw.pop("confidence", "high"),
        is_valid=kw.pop("is_valid", True),
        **kw,
    )


def _config_error(ctx: CodeContext, names: list[str]) -> Finding:
    return _f(
        ctx,
        "Unclear",
        "This rule cannot be checked yet because a rule setting has not been filled in.",
        error_flag=True,
        error_detail="Unfilled rule parameter(s): " + ", ".join(names),
        confidence="low",
        is_valid=False,
    )


def _placeholders(params: dict[str, Any], keys: list[str]) -> list[str]:
    bad = []
    for k in keys:
        v = params.get(k)
        if is_placeholder(v) or (isinstance(v, list) and any(is_placeholder(x) for x in v)):
            bad.append(f"{k}={v}")
    return bad


def _fact(ctx: CodeContext, key: str) -> tuple[str | None, Finding | None]:
    """Return (value, None) or (None, finding-to-return)."""
    fact = ctx.facts.get(key)
    if fact is None:
        if ctx.facts.error:
            return None, _f(
                ctx, "Unclear", f"The {key.replace('_', ' ')} could not be read automatically.",
                error_flag=True, error_detail=f"Fact extraction failed: {ctx.facts.error}", confidence="low", is_valid=False,
            )
        return None, _f(ctx, "Needs evidence", f"The application does not state the {key.replace('_', ' ')}.",
                        needs_applicant_clarification=True)
    if fact.fact_value == NOT_STATED:
        return None, _f(ctx, "Needs evidence", f"The application does not state the {key.replace('_', ' ')}.",
                        needs_applicant_clarification=True)
    if not fact.quote_verified:
        return None, _f(
            ctx, "Unclear", f"The {key.replace('_', ' ')} could not be matched to the applicant's own words.",
            error_detail="Fact quote not verified against the application text", confidence="low", is_valid=False,
        )
    return fact.fact_value, None


def _quote_kw(ctx: CodeContext, key: str) -> dict[str, Any]:
    fact = ctx.facts.get(key)
    return {"evidence_quote": fact.source_quote, "quote_verified": fact.quote_verified} if fact else {}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def documents_present(ctx: CodeContext) -> Finding:
    required: list[str] = ctx.rule.params.get("required_documents", [])
    if bad := _placeholders(ctx.rule.params, ["required_documents"]):
        return _config_error(ctx, bad)
    present = ctx.documents.present_types()
    missing = [t for t in required if t not in present]
    if not missing:
        return _f(ctx, "Met", "All required documents were provided: " + ", ".join(DOC_LABEL.get(t, t) for t in required) + ".")
    wrong = [
        f"a file uploaded as a {DOC_LABEL.get(c.declared_type, c.declared_type)} looks like a {DOC_LABEL.get(c.detected_type, 'different document')}"
        for c in ctx.documents.checks
        if not c.type_matches and c.declared_type in missing
    ]
    rationale = "Missing: " + ", ".join(DOC_LABEL.get(t, t) for t in missing) + "."
    if wrong:
        rationale += " Note: " + "; ".join(wrong) + "."
    return _f(ctx, "Needs evidence", rationale, needs_applicant_clarification=True)


def document_date_on_or_after(ctx: CodeContext) -> Finding:
    """A document date (e.g. visa expiry, course end) must be on/after a date."""
    p = ctx.rule.params
    if bad := _placeholders(p, ["on_or_after"]):
        return _config_error(ctx, bad)
    doc_type, date_field = p["document_type"], p["date_field"]
    threshold = parse_date(p["on_or_after"])
    if threshold is None:
        return _config_error(ctx, [f"on_or_after={p['on_or_after']} (not a date)"])
    docs = ctx.documents.by_type(doc_type)
    label = DOC_LABEL.get(doc_type, doc_type)
    if not docs:
        return _f(ctx, "Needs evidence", f"No {label} was provided.", needs_applicant_clarification=True)
    values = [d.extracted_fields.get(f"{date_field}_iso") for d in docs]
    dates = [date.fromisoformat(v) for v in values if v]
    if not dates:
        return _f(ctx, "Unclear", f"The {date_field.replace('_', ' ')} could not be read from the {label}.",
                  needs_applicant_clarification=True, confidence="low")
    best = max(dates)
    field_label = date_field.replace("_", " ")
    if best >= threshold:
        return _f(ctx, "Met", f"The {label} shows {field_label} {best:%d %B %Y}, which is on or after {threshold:%d %B %Y}.")
    return _f(ctx, "Not met", f"The {label} shows {field_label} {best:%d %B %Y}, which is before {threshold:%d %B %Y}.")


def fact_date_in_window(ctx: CodeContext) -> Finding:
    p = ctx.rule.params
    if bad := _placeholders(p, ["start", "end"]):
        return _config_error(ctx, bad)
    start, end = parse_date(p["start"]), parse_date(p["end"])
    if not start or not end:
        return _config_error(ctx, ["start/end (not dates)"])
    value, early = _fact(ctx, p["fact"])
    if early:
        return early
    d = parse_date(value)
    if d is None:
        return _f(ctx, "Unclear", f"The date given ({value}) could not be read as a date.",
                  needs_applicant_clarification=True, confidence="low", **_quote_kw(ctx, p["fact"]))
    inside = start <= d <= end
    return _f(
        ctx,
        "Met" if inside else "Not met",
        f"The date given is {d:%d %B %Y}; the window is {start:%d %B %Y} to {end:%d %B %Y}.",
        **_quote_kw(ctx, p["fact"]),
    )


def typed_matches_documents(ctx: CodeContext) -> Finding:
    comps = ctx.documents.comparisons()
    if not comps:
        return _f(ctx, "Needs evidence", "There were no document details to compare with the form.",
                  needs_applicant_clarification=True)
    flagged = [c for c in comps if c["result"] in ("mismatch", "unparseable")]
    variants = [c for c in comps if c["result"] == "variant"]
    if flagged:
        fields_ = sorted({c["field"].replace("_", " ") for c in flagged})
        return _f(
            ctx,
            "Needs evidence",
            "Needs verification: the " + ", ".join(fields_) + " on the form does not match the uploaded document. "
            "This may be a typing or format difference; please check with the applicant.",
            needs_applicant_clarification=True,
            confidence="medium",
        )
    note = ""
    if variants:
        note = " Minor differences accepted (name order, spelling variant or date format): " + ", ".join(
            sorted({c["field"].replace("_", " ") for c in variants})
        ) + "."
    return _f(ctx, "Met", "The details typed on the form match the uploaded documents." + note)


def fact_in_list(ctx: CodeContext) -> Finding:
    p = ctx.rule.params
    if bad := _placeholders(p, ["allowed"]):
        return _config_error(ctx, bad)
    value, early = _fact(ctx, p["fact"])
    if early:
        return early
    allowed: list[str] = p["allowed"]
    best = max(((a, fuzz.partial_ratio(a.lower(), value.lower())) for a in allowed), key=lambda x: x[1])
    if best[1] >= p.get("match_threshold", 90):
        return _f(ctx, "Met", f'The application states "{value}", which matches an eligible option ({best[0]}).',
                  **_quote_kw(ctx, p["fact"]))
    return _f(ctx, "Not met", f'The application states "{value}", which is not in the list of eligible options.',
              confidence="medium", **_quote_kw(ctx, p["fact"]))


def amount_at_most(ctx: CodeContext) -> Finding:
    p = ctx.rule.params
    if bad := _placeholders(p, ["max"]):
        return _config_error(ctx, bad)
    cap = parse_amount(p["max"])
    if cap is None:
        return _config_error(ctx, [f"max={p['max']} (not an amount)"])
    value, early = _fact(ctx, p["fact"])
    if early:
        return early
    amount = parse_amount(value)
    if amount is None:
        return _f(ctx, "Unclear", f"The amount given ({value}) could not be read as a number.",
                  needs_applicant_clarification=True, confidence="low", **_quote_kw(ctx, p["fact"]))
    ok = amount <= cap
    return _f(ctx, "Met" if ok else "Not met",
              f"The amount requested is ${amount:,.2f}; the maximum is ${cap:,.2f}.", **_quote_kw(ctx, p["fact"]))


def max_active_grants(ctx: CodeContext) -> Finding:
    p = ctx.rule.params
    if bad := _placeholders(p, ["max_active_grants", "counts_this_application"]):
        return _config_error(ctx, bad)
    limit = int(p["max_active_grants"])
    # scope "program" (default): only grants from the same program count.
    rows = ctx.register_rows
    if p.get("scope", "program") == "program":
        rows = [r for r in rows if r.get("grant_program_id") == ctx.grant_program_id]
    active = [r for r in rows if r.get("status") == "active"]
    total = len(active) + (1 if p.get("counts_this_application", True) else 0)
    ok = total <= limit
    detail = f"The register shows {len(active)} active grant(s) for this applicant"
    detail += "; with this application that would be " + str(total) if p.get("counts_this_application", True) else ""
    return _f(ctx, "Met" if ok else "Not met", f"{detail}. The limit is {limit}.")


CHECKS: dict[str, Callable[[CodeContext], Finding]] = {
    "documents_present": documents_present,
    "document_date_on_or_after": document_date_on_or_after,
    "fact_date_in_window": fact_date_in_window,
    "typed_matches_documents": typed_matches_documents,
    "fact_in_list": fact_in_list,
    "amount_at_most": amount_at_most,
    "max_active_grants": max_active_grants,
}


def run_code_check(ctx: CodeContext) -> Finding:
    name = ctx.rule.params.get("check")
    fn = CHECKS.get(name or "")
    if fn is None:
        return _f(ctx, "Unclear", "This rule has no automatic check configured.", error_flag=True,
                  error_detail=f"Unknown code check: {name}", confidence="low", is_valid=False)
    try:
        return fn(ctx)
    except (KeyError, ValueError, TypeError) as exc:
        return _f(ctx, "Unclear", "This rule could not be checked automatically.", error_flag=True,
                  error_detail=f"Check {name} failed: {type(exc).__name__}", confidence="low", is_valid=False)
