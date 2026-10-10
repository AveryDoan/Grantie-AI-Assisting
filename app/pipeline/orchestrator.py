"""Runs the assessment pipeline end to end.

`assess_application` is pure (no writes) so the evaluation harness can use
it directly. `run_assessment` adds access checks, idempotency, persistence
and audit for the API.

Every LLM call goes through redaction first: the text is redacted and
leak-scanned by the `redaction` package, and the LLM client is wrapped in
GuardedLLM, which refuses anything that did not pass. If redaction blocks
(leak found, or low-confidence detections in "block" mode) the assessment
stops before any LLM call - it fails closed.

The pipeline only produces suggestions. Nothing here approves, rejects,
scores or ranks an application.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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
from app.pipeline.referees import extract_referee_letters
from app.pipeline.rules_loader import RulePackError, RulePackNotApproved, load_rule_pack
from app.pipeline.verification import verify_findings
from app.services.access import Actor, application_for_staff
from app.services.audit import write_audit
from app.services.errors import Conflict, ManualAssessmentRequested
from app.services.redaction_service import RedactionBlockedError, extracted_documents, document_kinds, run_and_store
from app.store.base import Store, now_iso, one

log = get_logger(__name__)

ASSESSABLE = ("submitted", "in_review", "awaiting_applicant")


REFEREE_CHECKS = {"referee_fields", "referee_dates"}


def _needs_referees(pack: RulePack) -> bool:
    return any(r.params.get("check") in REFEREE_CHECKS or r.params.get("evidence_source") == "referee_letters"
               for r in pack.rules)


def load_reference_lists(store: Store, pack: RulePack) -> dict[str, list[str] | None]:
    """Official lookup lists named by the pack's rules. Missing = not loaded."""
    names = sorted({r.params["list"] for r in pack.rules if r.params.get("list")})
    if not names:
        return {}
    rows = {row["name"]: row.get("items") or [] for row in store.select("reference_lists", in_={"name": names})}
    return {n: rows.get(n) for n in names}


@dataclass
class AssessmentOutcome:
    rule_pack: RulePack
    redaction: Any  # redaction.pipeline.RedactionOutcome
    injection_flags: list[InjectionFlag]
    document_checks: DocumentChecks
    facts: FactSet
    findings: list[Finding]
    input_hash: str
    model_name: str
    prompt_version: str
    referees: list = field(default_factory=list)


def compute_input_hash(
    redacted_text: str, documents: list[dict[str, Any]], pack_version: str, model_name: str, consistency: bool,
    reference_lists: dict[str, Any] | None = None, register_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Changes whenever anything that can change a finding changes."""
    docs = sorted((d.get("id", ""), d.get("declared_type", ""), d.get("extracted_text") or "") for d in documents)
    register = sorted((r.get("record_type", ""), r.get("record_name") or "", r.get("status", ""), str(r.get("grant_program_id")))
                      for r in register_rows or [])
    payload = json.dumps([redacted_text, docs, pack_version, PROMPT_VERSION, model_name, consistency,
                          reference_lists or {}, register], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def redact_for_assessment(application: dict[str, Any], documents: list[dict[str, Any]]):
    """Pure redaction (no persistence), used when no stored outcome is passed in."""
    from redaction.pipeline import run_redaction

    extracted = extracted_documents(None, documents)
    return run_redaction(application.get("id", "unsaved"), application.get("application_text") or {},
                         [d for _, d, _ in extracted], document_kinds=document_kinds(extracted))


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
    reference_lists: dict[str, list[str] | None] | None = None,
    redaction: Any = None,
) -> AssessmentOutcome:
    from redaction.pipeline import GuardedLLM

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

    # Redaction first. Nothing below may send unredacted text to the LLM.
    redaction = redaction or redact_for_assessment(application, documents)
    if not redaction.llm_allowed:
        raise RedactionBlockedError(
            f"AI assessment stopped: redaction status is '{redaction.ai_status}'. An officer must review the application.",
            details={"ai_status": redaction.ai_status, "leak_scan": redaction.report.get("leak_scan", {})},
        )
    llm = GuardedLLM(llm, redaction) if llm is not None else None
    redacted_text = redaction.redacted_text

    # Local, code-only checks read the original text; it never leaves this process.
    flags = screen_application(app_text, documents)
    doc_checks = check_documents(typed, documents, name_threshold=settings.name_match_threshold)
    facts = extract_facts(
        llm, pack.rules, typed, redacted_text, pack.version, threshold=settings.quote_fuzzy_threshold
    )
    referees: list = []
    if _needs_referees(pack):
        letters = [d for d in documents if any(c.document_id == d["id"] and c.detected_type == "referee_letter" for c in doc_checks.checks)]
        doc_texts = {d.document_id: d.redacted_text for d in redaction.documents if d.included_in_ai_input}
        referees = extract_referee_letters(llm, letters, pack.version, redacted_texts=doc_texts,
                                           threshold=settings.quote_fuzzy_threshold)
    findings = evaluate_rules(
        llm,
        pack.rules,
        redacted_text=redacted_text,
        pack_version=pack.version,
        facts=facts,
        documents=doc_checks,
        register_rows=register_rows,
        grant_program_id=application.get("grant_program_id"),
        application=application,
        reference_lists=reference_lists or {},
        referees=referees,
        raw_documents=documents,
    )
    if use_consistency:
        findings = consistency_check(llm, pack.rules, findings, redacted_text, pack.version)
    findings = verify_findings(
        findings,
        pack.rules,
        redacted_text,
        threshold=settings.quote_fuzzy_threshold,
        min_fuzzy_length=settings.quote_min_fuzzy_length,
        extra_sources={f"document:{r.document_id}": r.redacted_text for r in referees},
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
        input_hash=compute_input_hash(redacted_text, documents, pack.version, model_name, use_consistency,
                                      reference_lists, register_rows),
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
        referees=referees,
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

    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1))
    register_rows = store.select("mock_grants_register", eq={"applicant_id": app["applicant_id"]})
    use_consistency = settings.consistency_check if consistency is None else consistency
    model_name = llm.model_name if llm else "none"

    # Redact (and store the encrypted token map) before anything else. Fail closed.
    redacted = run_and_store(store, actor, application_id, settings)
    redaction = redacted["outcome"]
    if not redaction.llm_allowed:
        write_audit(store, actor, "assessment.refused", application_id=application_id,
                    details={"reason": redaction.ai_status, "redaction_run_id": redacted["run"]["id"]})
        raise RedactionBlockedError(
            f"AI assessment stopped: redaction status is '{redaction.ai_status}'. An officer must review the application.",
            details={"ai_status": redaction.ai_status, "leak_scan": redaction.report.get("leak_scan", {})},
        )
    documents = [d for d in store.select("documents", eq={"application_id": application_id}) if not d.get("superseded")]

    # Idempotency: same inputs -> same run (reviews are kept).
    reference_lists = load_reference_lists(store, pack)
    input_hash = compute_input_hash(redaction.redacted_text, documents, pack.version, model_name, use_consistency,
                                    reference_lists, register_rows)
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
            reference_lists=reference_lists,
            redaction=redaction,
        )
        _persist(store, app, run, outcome, documents)
        if settings.consistency_layer:
            # Never raises, never changes a rule result, never blocks sign-off: flags are for the officer to check.
            from app.services import consistency as consistency_service

            consistency_service.run_for_assessment(store, actor, app, run, outcome, documents, pack, llm, settings)
    except Exception as exc:
        from redaction.pipeline import RedactionBlocked

        log.exception("assessment run %s failed", run["id"])
        message = f"{type(exc).__name__}"  # no application text in errors
        store.update("assessment_runs", {"status": "failed", "finished_at": now_iso(), "error_message": message},
                     eq={"id": run["id"]})
        write_audit(store, actor, "assessment.failed", application_id=application_id,
                    rule_pack_version=pack.version, details={"run_id": run["id"], "error": message})
        if isinstance(exc, RedactionBlocked):
            raise RedactionBlockedError(str(exc)) from exc
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
    # Redacted text and the encrypted token map were stored by the redaction service.
    store.update("applications", {"status": "in_review"}, eq={"id": app_id})

    for chk in outcome.document_checks.checks:
        store.update(
            "documents",
            {
                "detected_type": chk.detected_type,
                "type_matches": chk.type_matches,
                "extracted_fields": chk.extracted_fields,
                "needs_verification": chk.needs_verification,
                "verification_notes": chk.comparisons,
                "attention_level": chk.attention_level,
                "attention_reason": chk.attention_reason,
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
        for f in [*outcome.facts.facts.values(), *(f for r in outcome.referees for f in r.fields.values())]
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
