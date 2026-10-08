"""Pipeline step 7: verification, by plain code (never the LLM).

- Every quote must appear in the source text: exact match first, then a
  normalised match (whitespace, curly quotes, case), then fuzzy partial
  match at a configurable threshold (default 92) for quotes long enough to
  be meaningful.
- Every rule must have exactly one finding.
- Output has already been validated against the Pydantic schema by
  call_llm; anything that failed became an error finding.
Failures set is_valid = false. Invalid findings are never shown as valid.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.domain import Finding, Rule

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_CHARS)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text.strip(" \"'.,;:")


@dataclass(frozen=True)
class QuoteCheck:
    verified: bool
    method: str  # exact | normalised | fuzzy | none
    score: float | None = None


def verify_quote(quote: str | None, source: str, *, threshold: float = 92.0, min_fuzzy_length: int = 12) -> QuoteCheck:
    if not quote or not quote.strip():
        return QuoteCheck(False, "none")
    if quote in source:
        return QuoteCheck(True, "exact", 100.0)
    nq, ns = normalise(quote), normalise(source)
    if nq and nq in ns:
        return QuoteCheck(True, "normalised", 100.0)
    if len(nq) >= min_fuzzy_length:
        score = fuzz.partial_ratio(nq, ns)
        if score >= threshold:
            return QuoteCheck(True, "fuzzy", score)
        return QuoteCheck(False, "none", score)
    return QuoteCheck(False, "none")


def verify_findings(
    findings: list[Finding],
    rules: list[Rule],
    source: str,
    *,
    threshold: float = 92.0,
    min_fuzzy_length: int = 12,
) -> list[Finding]:
    """Verify quotes and completeness. Returns exactly one finding per rule."""
    by_rule: dict[str, Finding] = {}
    duplicates: set[str] = set()
    for f in findings:
        if f.rule_id in by_rule:
            duplicates.add(f.rule_id)
            continue
        by_rule[f.rule_id] = f

    out: list[Finding] = []
    for rule in rules:
        f = by_rule.get(rule.id)
        if f is None:
            f = Finding(
                rule_id=rule.id,
                rule_code=rule.rule_code,
                ai_status="Evidence only" if rule.evidence_only else "Unclear",
                check_source=rule.check_method,
                error_flag=True,
                error_detail="No finding was produced for this rule",
            )
        if f.evidence_quote is not None:
            qc = verify_quote(f.evidence_quote, source, threshold=threshold, min_fuzzy_length=min_fuzzy_length)
            f.quote_verified = qc.verified
            if not qc.verified:
                f.is_valid = False
                f.error_detail = (f.error_detail + "; " if f.error_detail else "") + "Quote not found in the application text"
        if f.supporting_quotes:
            for sq in f.supporting_quotes:
                qc = verify_quote(sq.get("quote"), source, threshold=threshold, min_fuzzy_length=min_fuzzy_length)
                sq["verified"] = qc.verified
                sq["method"] = qc.method
            if not all(sq["verified"] for sq in f.supporting_quotes):
                f.is_valid = False
                f.error_detail = (f.error_detail + "; " if f.error_detail else "") + "One or more supporting quotes were not found in the application text"
        if rule.id in duplicates:
            f.is_valid = False
            f.error_detail = (f.error_detail + "; " if f.error_detail else "") + "More than one finding was produced for this rule"
        out.append(f.enforce_invariants())
    return out
