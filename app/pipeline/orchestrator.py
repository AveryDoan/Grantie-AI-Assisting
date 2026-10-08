"""Runs the assessment pipeline end to end.

`assess_application` is pure (no writes) so the evaluation harness can use
it directly. `run_assessment` adds access checks, idempotency, persistence
and audit for the API.

The pipeline only produces suggestions. Nothing here approves, rejects,
scores or ranks an application.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.domain import Finding, RulePack
from app.llm import LLMClient
from app.logging_utils import get_logger
from app.pipeline.documents import DocumentChecks, check_documents
from app.pipeline.evaluate import consistency_check, evaluate_rules
from app.pipeline.facts import FactSet, extract_facts
from app.pipeline.injection import InjectionFlag, screen_application
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.redaction import RedactionResult, redact
from app.pipeline.rules_loader import RulePackError, RulePackNotApproved, load_rule_pack
from app.pipeline.verification import verify_findings
from app.services.access import Actor, application_for_staff
from app.services.audit import write_audit
from app.services.errors import Conflict, ManualAssessmentRequested
from app.store.base import Store, now_iso, one

log = get_logger(__name__)

ASSESSABLE = ("submitted", "in_review", "awaiting_applicant")


@dataclass
class AssessmentOutcome:
    rule_pack: RulePack
    redaction: RedactionResult
    injection_flags: list[InjectionFlag]
    document_checks: DocumentChecks
    facts: FactSet
    findings: list[Finding]
    input_hash: str
    model_name: str
    prompt_version: str


def compute_input_hash(
    redacted_text: str, documents: list[dict[str, Any]], pack_version: str, model_name: str, consistency: bool
) -> str:
    docs = sorted((d.get("id", ""), d.get("declared_type", ""), d.get("extracted_text") or "") for d in documents)
    payload = json.dumps([redacted_text, docs, pack_version, PROMPT_VERSION, model_name, consistency], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _applicant_names(applicant: dict[str, Any] | None) -> list[str]:
    return [applicant["display_name"]] if applicant and applicant.get("display_name") else []


def assess_application(
    *,
    application: dict[str, Any],
    documents: list[dict[str, Any]],
    pack: RulePack,
    register_rows: list[dict[str, Any]],
    llm: LLMClient | None,
    settings: Settings,
    applicant: dict[str, Any] | None = None,
    consistency: bool | None = None,
) -> AssessmentOutcome:
    # Defence in depth: the API and the database both refuse too.
    if application.get("manual_assessment_requested"):
        raise ManualAssessmentRequested(
            "The applicant asked for a person to assess this application. AI assessment will not run."
        )
    if pack.status != "approved":
        raise RulePackNotApproved(f"rule pack {pack.version} is {pack.status}")

    app_text: dict[str, Any] = application.get("application_text") or {}
    typed = app_text.get("fields") or {}
    use_consistency = settings.consistency_check if consistency is None else consistency
    model_name = llm.model_name if llm else "none"

    redaction = redact(app_text, extra_names=_applicant_names(applicant))
    flags = screen_application(app_text, documents)
    doc_checks = check_documents(typed, documents, name_threshold=settings.name_match_threshold)
    facts = extract_facts(
        llm, pack.rules, typed, redaction.text, pack.version, threshold=settings.quote_fuzzy_threshold
    )
    findings = evaluate_rules(
        llm,
        pack.rules,
        redacted_text=redaction.text,
        pack_version=pack.version,
        facts=facts,
        documents=doc_checks,
        register_rows=register_rows,
        grant_program_id=application.get("grant_program_id"),
    )
    if use_consistency:
        findings = consistency_check(llm, pack.rules, findings, redaction.text, pack.version)
    findings = verify_findings(
        findings,
        pack.rules,
        redaction.text,
        threshold=settings.quote_fuzzy_threshold,
        min_fuzzy_length=settings.quote_min_fuzzy_length,
    )
    if flags:
        # Instruction-like text was found: never let it raise confidence.
        for f in findings:
            if f.check_source == "llm" and f.ai_status not in ("Evidence only",):
                f.confidence = "low"
                f.rationale = (f.rationale or "") + " [Instruction-like text was found in this application; see injection flags.]"
    return AssessmentOutcome(
        rule_pack=pack,
        redaction=redaction,
        injection_flags=flags,
        document_checks=doc_checks,
        facts=facts,
        findings=findings,
        input_hash=compute_input_hash(redaction.text, documents, pack.version, model_name, use_consistency),
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )


def run_assessment(
    store: Store,
    actor: Actor,
    application_id: str,
    llm: LLMClient | None,
    settings: Settings,
    *,
    force: bool = False,
    consistency: bool | None = None,
) -> dict[str, Any]:
    """Run (or reuse) an assessment. Returns the run row plus a `reused` flag."""
    app = application_for_staff(store, actor, application_id)

    if app.get("manual_assessment_requested"):
        write_audit(store, actor, "assessment.refused", application_id=application_id,
                    details={"reason": "manual_assessment_requested"})
        raise ManualAssessmentRequested(
            "The applicant asked for a person to assess this application. AI assessment will not run."
        )
    if app["status"] not in ASSESSABLE:
        raise Conflict(f"Cannot assess an application with status '{app['status']}'")

    try:
        pack = load_rule_pack(store, app["rule_pack_id"])
    except RulePackNotApproved as exc:
        write_audit(store, actor, "assessment.refused", application_id=application_id,
                    details={"reason": "rule_pack_not_approved"})
        raise Conflict(str(exc)) from exc
    except RulePackError as exc:
        raise Conflict(str(exc)) from exc

    documents = store.select("documents", eq={"application_id": application_id})
    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1))
    register_rows = store.select("mock_grants_register", eq={"applicant_id": app["applicant_id"]})
    use_consistency = settings.consistency_check if consistency is None else consistency
    model_name = llm.model_name if llm else "none"

    # Idempotency: same inputs -> same run (reviews are kept).
    redacted_preview = redact(app.get("application_text") or {}, extra_names=_applicant_names(applicant)).text
    input_hash = compute_input_hash(redacted_preview, documents, pack.version, model_name, use_consistency)
    if not force:
        previous = store.select(
            "assessment_runs",
            eq={"application_id": application_id, "input_hash": input_hash, "status": "complete"},
            order="finished_at",
            desc=True,
            limit=1,
        )
        if previous:
            return previous[0] | {"reused": True}

    run = store.insert(
        "assessment_runs",
        {
            "application_id": application_id,
            "rule_pack_id": pack.id,
            "rule_pack_version": pack.version,
            "model_name": model_name,
            "prompt_version": PROMPT_VERSION,
            "consistency_check": use_consistency,
            "triggered_by": actor.user_id,
            "input_hash": input_hash,
            "status": "running",
        },
    )[0]
    write_audit(store, actor, "assessment.started", application_id=application_id,
                rule_pack_version=pack.version, details={"run_id": run["id"], "model": model_name})

    try:
        outcome = assess_application(
            application=app,
            documents=documents,
            pack=pack,
            register_rows=register_rows,
            llm=llm,
            settings=settings,
            applicant=applicant,
            consistency=use_consistency,
        )
        _persist(store, app, run, outcome, documents)
    except Exception as exc:
        log.exception("assessment run %s failed", run["id"])
        message = f"{type(exc).__name__}"  # no application text in errors
        store.update("assessment_runs", {"status": "failed", "finished_at": now_iso(), "error_message": message},
                     eq={"id": run["id"]})
        write_audit(store, actor, "assessment.failed", application_id=application_id,
                    rule_pack_version=pack.version, details={"run_id": run["id"], "error": message})
        raise

    summary = {
        "findings": len(outcome.findings),
        "invalid": sum(not f.is_valid for f in outcome.findings),
        "errors": sum(f.error_flag for f in outcome.findings),
        "language_flags": sum(f.language_flag for f in outcome.findings),
        "injection_flags": len(outcome.injection_flags),
    }
    write_audit(store, actor, "assessment.completed", application_id=application_id,
                rule_pack_version=pack.version, details={"run_id": run["id"], **summary})
    done = one(store.select("assessment_runs", eq={"id": run["id"]}, limit=1)) or run
    return done | {"reused": False, "summary": summary}


def _persist(
    store: Store, app: dict[str, Any], run: dict[str, Any], outcome: AssessmentOutcome, documents: list[dict[str, Any]]
) -> None:
    app_id = app["id"]
    store.update(
        "applications",
        {"redacted_text": outcome.redaction.text, "status": "in_review"},
        eq={"id": app_id},
    )
    if store.select("redaction_maps", eq={"application_id": app_id}, limit=1):
        store.update("redaction_maps", {"mapping": outcome.redaction.mapping}, eq={"application_id": app_id})
    else:
        store.insert("redaction_maps", {"application_id": app_id, "mapping": outcome.redaction.mapping})

    for chk in outcome.document_checks.checks:
        store.update(
            "documents",
            {
                "detected_type": chk.detected_type,
                "type_matches": chk.type_matches,
                "extracted_fields": chk.extracted_fields,
                "needs_verification": chk.needs_verification,
                "verification_notes": chk.comparisons,
            },
            eq={"id": chk.document_id},
        )

    fact_rows = [
        {
            "application_id": app_id,
            "run_id": run["id"],
            "fact_key": f.fact_key,
            "fact_value": f.fact_value,
            "source_quote": f.source_quote,
            "quote_verified": f.quote_verified,
            "source": f.source,
        }
        for f in outcome.facts.facts.values()
    ]
    if fact_rows:
        store.insert("fact_extractions", fact_rows)

    finding_rows = [
        f.model_dump(exclude={"rule_code"}) | {"run_id": run["id"], "application_id": app_id}
        for f in outcome.findings
    ]
    store.insert("findings", finding_rows)

    store.update(
        "assessment_runs",
        {
            "status": "complete",
            "finished_at": now_iso(),
            "injection_flags": [fl.to_dict() for fl in outcome.injection_flags],
        },
        eq={"id": run["id"]},
    )
