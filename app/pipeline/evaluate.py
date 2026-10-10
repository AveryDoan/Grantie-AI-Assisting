"""Pipeline steps 6 and 8: rule evaluation and the optional consistency check.

Routing:
- judgement rules or check_method = human_only -> supporting quotes only,
  status "Evidence only" (the schema has no status field at all);
- check_method = code -> app.pipeline.code_checks;
- check_method = llm  -> RuleAssessmentOut validated by Pydantic.
Any LLM failure becomes "Unclear" + error_flag (never "Met").
"""

from __future__ import annotations

from app.domain import Finding, Rule
from app.llm import LLMClient, LLMError
from app.pipeline.code_checks import CodeContext, run_code_check
from app.pipeline.documents import DocumentChecks
from app.pipeline.facts import FactSet
from app.pipeline.prompts import MAX_QUOTES, MAX_SUMMARIES, EvidenceOut, RuleAssessmentOut, evidence_prompt, rule_prompt


def _llm_failure(rule: Rule, exc: Exception | str) -> Finding:
    return Finding(
        rule_id=rule.id,
        rule_code=rule.rule_code,
        ai_status="Evidence only" if rule.evidence_only else "Unclear",
        check_source=rule.check_method,
        error_flag=True,
        error_detail=f"AI check failed: {exc}",
        rationale=None if rule.evidence_only else "The automatic check failed. An officer needs to assess this rule.",
    )


def evidence_from_referees(rule: Rule, referees: list | None) -> Finding:
    """M2: supporting evidence = the facts and quotes extracted from each referee letter (no AI judgement)."""
    letters = referees or []
    failed = [l for l in letters if l.error]
    if failed:
        return _llm_failure(rule, failed[0].error)
    quotes: list[dict] = []
    for l in letters:
        for key in ("referee_name", "position", "organisation", "relationship", "length_of_association"):
            f = l.fields.get(key)
            if f and f.source_quote:
                quotes.append({"quote": f.source_quote, "source": f.source, "label": key.replace("_", " ")})
        quotes.extend({**h, "label": "about the applicant"} for h in l.highlights)
    return Finding(rule_id=rule.id, rule_code=rule.rule_code, ai_status="Evidence only", supporting_quotes=quotes,
                   check_source=rule.check_method, is_valid=True)


def evaluate_evidence_only(llm: LLMClient | None, rule: Rule, redacted_text: str, pack_version: str) -> Finding:
    if llm is None:
        return _llm_failure(rule, "no LLM configured")
    try:
        out = llm.call_llm(evidence_prompt(rule, redacted_text, pack_version), EvidenceOut)
    except LLMError as exc:
        return _llm_failure(rule, exc)
    quotes = [{"quote": q} for q in dict.fromkeys(q.strip() for q in out.supporting_quotes) if q][:MAX_QUOTES]
    summaries = [{"text": sm.summary.strip(), "passages": [{"quote": p.strip()} for p in dict.fromkeys(sm.passages) if p.strip()][:2]}
                 for sm in out.summaries if sm.summary.strip()][:MAX_SUMMARIES]
    return Finding(
        rule_id=rule.id,
        rule_code=rule.rule_code,
        ai_status="Evidence only",
        supporting_quotes=quotes,
        ai_summaries=summaries,
        check_source=rule.check_method,
        is_valid=True,
        # No rationale, no confidence: no conclusion (principle 6).
    )


def evaluate_llm_rule(
    llm: LLMClient | None, rule: Rule, redacted_text: str, pack_version: str, *, alt: bool = False
) -> Finding:
    if llm is None:
        return _llm_failure(rule, "no LLM configured")
    try:
        out = llm.call_llm(rule_prompt(rule, redacted_text, pack_version, alt=alt), RuleAssessmentOut)
    except LLMError as exc:
        return _llm_failure(rule, exc)
    finding = Finding(
        rule_id=rule.id,
        rule_code=rule.rule_code,
        ai_status=out.status,
        rationale=out.rationale,
        evidence_quote=out.evidence_quote.strip() if out.evidence_quote and out.evidence_quote.strip() else None,
        confidence=out.confidence,
        language_flag=out.language_flag,
        needs_applicant_clarification=out.needs_applicant_clarification or out.language_flag,
        check_source="llm",
        is_valid=True,
    )
    # Invariants (language flag -> Unclear, etc.) are applied in verify_findings,
    # after the quote has been checked; applying them here would mark every
    # not-yet-verified quote invalid.
    if finding.language_flag:
        finding.ai_status = "Unclear"
    return finding


def evaluate_rules(
    llm: LLMClient | None,
    rules: list[Rule],
    *,
    redacted_text: str,
    pack_version: str,
    facts: FactSet,
    documents: DocumentChecks,
    register_rows: list[dict],
    grant_program_id: str | None = None,
    application: dict | None = None,
    reference_lists: dict | None = None,
    referees: list | None = None,
    raw_documents: list[dict] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        if rule.evidence_only and rule.params.get("evidence_source") == "referee_letters":
            findings.append(evidence_from_referees(rule, referees))
        elif rule.evidence_only:
            findings.append(evaluate_evidence_only(llm, rule, redacted_text, pack_version))
        elif rule.check_method == "code":
            ctx = CodeContext(rule, facts, documents, register_rows, grant_program_id, application or {},
                              reference_lists or {}, referees, raw_documents or [])
            findings.append(run_code_check(ctx))
        else:
            findings.append(evaluate_llm_rule(llm, rule, redacted_text, pack_version))
    return findings


def consistency_check(
    llm: LLMClient | None, rules: list[Rule], findings: list[Finding], redacted_text: str, pack_version: str
) -> list[Finding]:
    """Re-ask LLM-status rules with different wording; disagreement -> low confidence."""
    if llm is None:
        return findings
    by_id = {r.id: r for r in rules}
    for f in findings:
        rule = by_id[f.rule_id]
        if rule.evidence_only or rule.check_method != "llm" or f.error_flag:
            continue
        second = evaluate_llm_rule(llm, rule, redacted_text, pack_version, alt=True)
        if second.error_flag:
            f.rationale = (f.rationale or "") + " [Consistency check could not run.]"
            continue
        if second.ai_status != f.ai_status:
            f.confidence = "low"
            f.rationale = (f.rationale or "") + (
                f' [Consistency check: a second wording of the question gave "{second.ai_status}". Treat with care.]'
            )
    return findings
