"""FastAPI application: decision support for grants officers.

The AI only suggests. Officers confirm or override every finding and sign
off every decision. No endpoint returns an eligibility score, a ranking, or
an approve/reject recommendation, and there is no bulk-approve endpoint.

Run:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.api.auth import current_actor, get_store_dep
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    AssessRequest,
    ClarificationRequestIn,
    DemoLogin,
    AddMissedIn,
    DocumentDecisionIn,
    LocateIn,
    MeritMarkIn,
    ReopenIn,
    ReplyIn,
    EvidenceDraftIn,
    RequestDraftIn,
    RequestEditIn,
    RevealIn,
    UnmaskIn,
    ReferenceListSave,
    ReferenceListText,
    DocumentUpload,
    DraftIn,
    FlagReview,
    LetterPatch,
    PrecheckRequest,
    RedactRequest,
    ReviewRequest,
    SignOffRequest,
    SubmitIn,
)
from app.config import Settings, get_settings
from app.llm import LLMClient, LLMError, build_llm_client
from app.llm.cache import StoreCache
from app.logging_utils import configure_logging, get_logger
from app.pipeline.orchestrator import run_assessment
from app.services import audit, consistency, doc_step, evidence_request, intake, letters, merit, outcome, pdf_view, precheck, queue, redaction_check, redaction_service, reference_lists, review, steps
from app.services.access import Actor, application_for_staff, require_role
from app.services.errors import ServiceError
from app.store.base import Store, StoreError, one

log = get_logger(__name__)


def create_app(
    *,
    store: Store | None = None,
    llm_factory: Callable[[Store, Settings], LLMClient | None] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()
    _store: dict[str, Store] = {}

    demo_users: dict[str, dict] = {}
    if settings.app_mode == "demo" and store is None:
        from app.demo import build_demo, demo_llm_factory
        from app.llm.cache import MemoryCache

        store, settings, demo_users = build_demo(settings)
        llm_factory = llm_factory or demo_llm_factory(MemoryCache())
        log.warning("DEMO MODE: in-memory synthetic data and demo logins. Never use with real data.")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Data retention: purge cached LLM inputs/outputs older than LLM_RETENTION_DAYS.
        if store is None and settings.supabase_url:
            try:
                n = get_store().rpc("purge_expired_llm_data", {"p_retention_days": settings.llm_retention_days})
                log.info("retention purge removed %s cached LLM rows", n)
            except Exception:
                log.warning("retention purge skipped")
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="AI Application for Study NT Grant - officer decision support",
        version="0.1.0",
        description="Decision support only. Officers decide. Synthetic data only.",
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
        )

    def get_store() -> Store:
        if "s" not in _store:
            if store is not None:
                _store["s"] = store
            else:
                from app.store.supabase_store import SupabaseStore

                _store["s"] = SupabaseStore(settings)
        return _store["s"]

    def default_llm_factory(s: Store, cfg: Settings) -> LLMClient | None:
        return build_llm_client(cfg, cache=StoreCache(s, cfg.llm_retention_days))

    make_llm = llm_factory or default_llm_factory

    app.dependency_overrides[get_store_dep] = get_store
    app.dependency_overrides[get_settings] = lambda: settings

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, exc: StoreError) -> JSONResponse:
        # Trigger messages describe the rule that was broken; they contain no personal data.
        return JSONResponse(status_code=409, content={"error": "rejected_by_database", "message": str(exc)})

    general = Depends(rate_limit("general", "rate_limit_per_minute"))
    r = APIRouter(dependencies=[general])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/documents/{document_id}/file", response_model=None)
    def document_file(document_id: str, token: str, s: Store = Depends(get_store)) -> Any:
        """The original PDF, for a link that was signed a few minutes ago (officer role is checked when it is issued)."""
        data, name = pdf_view.file_bytes(s, settings, document_id, token)
        return Response(content=data, media_type="application/pdf", headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})

    if demo_users:
        from app.demo import demo_token

        @app.post("/demo/login", dependencies=[general])
        def demo_login(body: DemoLogin) -> dict[str, Any]:
            user = demo_users.get(body.role)
            if not user:
                return JSONResponse(status_code=404, content={"error": "unknown_demo_role"})  # type: ignore[return-value]
            return {"access_token": demo_token(settings, user["id"]), "role": body.role,
                    "display_name": user["display_name"], "demo": True}

        @app.post("/demo/applicant-reply/{application_id}", dependencies=[general])
        def demo_applicant_reply(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
            """DEMO ONLY: stand in for the applicant and reply to the officer's open request with fictional files."""
            require_role(actor, "officer")
            return doc_step.demo_reply(s, actor, application_id, settings)

    @r.get("/me")
    def me(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        profile = one(s.select("profiles", eq={"id": actor.user_id}, limit=1)) or {}
        return {"user_id": actor.user_id, "role": actor.role, "display_name": profile.get("display_name"),
                "organisation_id": actor.organisation_id}

    # ---------------------------------------------------------------- queue
    @r.get("/applications")
    def list_applications(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        return queue.list_queue(s, actor, settings)

    @r.post("/applications/precheck")
    def precheck_application(
        body: PrecheckRequest, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)
    ) -> dict[str, Any]:
        return precheck.precheck(
            s, actor, grant_program_id=body.grant_program_id, fields=body.fields,
            documents=[d.model_dump() for d in body.documents],
        )

    @r.get("/applications/{application_id}")
    def get_application(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return queue.application_detail(s, actor, application_id, settings)

    # ------------------------------------------------------------ pipeline
    @r.post("/applications/{application_id}/assess",
            dependencies=[Depends(rate_limit("assess", "rate_limit_assess_per_minute"))])
    def assess(
        application_id: str,
        body: AssessRequest | None = None,
        actor: Actor = Depends(current_actor),
        s: Store = Depends(get_store),
    ) -> dict[str, Any]:
        require_role(actor, "officer", "admin")
        body = body or AssessRequest()
        steps.assert_ai_allowed(s, actor, application_for_staff(s, actor, application_id), settings)   # step 2 must be approved
        try:
            llm = make_llm(s, settings)
        except LLMError as exc:
            return JSONResponse(status_code=503, content={"error": "llm_not_configured", "message": str(exc)})  # type: ignore[return-value]
        return run_assessment(s, actor, application_id, llm, settings, force=body.force, consistency=body.consistency_check)

    # ------------------------------------------------------- officer review
    @r.post("/findings/{finding_id}/review")
    def review_finding(
        finding_id: str, body: ReviewRequest, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)
    ) -> dict[str, Any]:
        return review.review_finding(s, actor, finding_id, action=body.action, final_status=body.final_status, reason=body.reason)

    @r.post("/applications/{application_id}/clarification")
    def clarification(
        application_id: str, body: ClarificationRequestIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)
    ) -> dict[str, Any]:
        return review.create_clarification(
            s, actor, application_id, finding_id=body.finding_id, message_text=body.message_text,
            send=body.send, clarification_id=body.clarification_id,
        )

    @r.post("/applications/{application_id}/signoff")
    def signoff(
        application_id: str, body: SignOffRequest, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)
    ) -> dict[str, Any]:
        return review.sign_off(s, actor, application_id, statement_acknowledged=body.statement_acknowledged)

    @r.get("/signoff-statement")
    def signoff_statement() -> dict[str, str]:
        return {"statement": review.SIGN_OFF_STATEMENT}

    # -------------------------------------------------------------- letters
    @r.post("/applications/{application_id}/letter")
    def generate_letter(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return letters.generate_letter(s, actor, application_id, settings)

    @r.patch("/letters/{letter_id}")
    def patch_letter(
        letter_id: str, body: LetterPatch, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)
    ) -> dict[str, Any]:
        return letters.update_letter(s, actor, letter_id, body_text=body.body_text, approve=body.approve)

    # ------------------------------------------------------------ redaction
    @r.post("/applications/{application_id}/redact",
            dependencies=[Depends(rate_limit("assess", "rate_limit_assess_per_minute"))])
    def redact(
        application_id: str, body: RedactRequest | None = None,
        actor: Actor = Depends(current_actor), s: Store = Depends(get_store),
    ) -> dict[str, Any]:
        """Run the redaction pipeline (idempotent). Returns status and counts only - no values."""
        require_role(actor, "officer", "admin")
        out = redaction_service.run_and_store(s, actor, application_id, settings, force=bool(body and body.force))
        outcome = out["outcome"]
        return {"run_id": out["run"]["id"], "reused": out["reused"], "report": outcome.report,
                "documents_needing_manual_review": outcome.manual_review}

    @r.get("/applications/{application_id}/redaction-report")
    def redaction_report(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        require_role(actor, "officer", "admin")
        return redaction_service.report(s, actor, application_id)

    @r.get("/applications/{application_id}/original-view")
    def original_view(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """Officers only: the stored redacted text restored with the encrypted token map. Audited."""
        return redaction_service.original_view(s, actor, application_id, settings)

    # ---------------------------------------------------- consistency flags
    @r.post("/consistency-flags/{flag_id}/review")
    def review_consistency_flag(flag_id: str, body: FlagReview, actor: Actor = Depends(current_actor),
                                s: Store = Depends(get_store)) -> dict[str, Any]:
        """Confirm or dismiss one flag. A dismissal needs a note. Audited. Never changes a rule result."""
        return consistency.review_flag(s, actor, flag_id, body.action, body.note, settings)

    @r.get("/pool/linked-applications")
    def linked_applications(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        """Groups of applications that share an attribute. Names the attribute, never its value."""
        return consistency.linked_groups(s, actor, settings)

    # ---------------------------------------------------- the original PDF: signed link, locate, blur, burned-in export
    @r.get("/documents/{document_id}/viewer")
    def document_viewer(document_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """Page sizes and a short-lived signed link to the original PDF. Officers only."""
        return pdf_view.viewer(s, actor, document_id, settings)

    @r.post("/documents/{document_id}/locate")
    def document_locate(document_id: str, body: LocateIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return pdf_view.locate_items(s, actor, document_id, [i.model_dump() for i in body.items])

    @r.get("/documents/{document_id}/blur")
    def document_blur(document_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return pdf_view.blur_boxes(s, actor, document_id, settings)

    @r.get("/documents/{document_id}/redacted.pdf", response_model=None)
    def document_redacted_pdf(document_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> Any:
        """A copy for sharing: each page an image with opaque black boxes burned in. No overlay, no text layer."""
        return Response(content=pdf_view.burned_document(s, actor, document_id, settings), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="redacted-{document_id[:8]}.pdf"', "Cache-Control": "no-store"})

    @r.get("/applications/{application_id}/evidence-pack.pdf", response_model=None)
    def evidence_pack_pdf(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> Any:
        return Response(content=pdf_view.evidence_pack(s, actor, application_id, settings), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="evidence-pack-{application_id[:8]}.pdf"', "Cache-Control": "no-store"})

    # ---------------------------------------------------- the guided steps
    @r.get("/applications/{application_id}/steps")
    def get_steps(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return steps.state(s, application_for_staff(s, actor, application_id), settings)

    @r.get("/applications/{application_id}/documents-step")
    def documents_step(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return doc_step.view(s, actor, application_id)

    @r.post("/applications/{application_id}/documents-step/decision")
    def document_decision(application_id: str, body: DocumentDecisionIn, actor: Actor = Depends(current_actor),
                          s: Store = Depends(get_store)) -> dict[str, Any]:
        return doc_step.decide(s, actor, application_id, body.slot, body.decision, body.reason, settings)

    @r.post("/applications/{application_id}/documents-step/requests")
    def draft_document_request(application_id: str, body: RequestDraftIn, actor: Actor = Depends(current_actor),
                               s: Store = Depends(get_store)) -> dict[str, Any]:
        """A draft only. Nothing is sent until the officer approves and sends it."""
        return doc_step.draft_request(s, actor, application_id, [i.model_dump() for i in body.items])

    @r.get("/applications/{application_id}/evidence-request/items")
    def evidence_items(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        require_role(actor, "officer")
        application_for_staff(s, actor, application_id)
        return evidence_request.needs_evidence(s, application_id)

    @r.post("/applications/{application_id}/evidence-request")
    def evidence_draft(application_id: str, body: EvidenceDraftIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """A draft only. This app sends nothing."""
        return evidence_request.draft(s, actor, application_id, body.finding_ids)

    @r.patch("/document-requests/{request_id}")
    def edit_document_request(request_id: str, body: RequestEditIn, actor: Actor = Depends(current_actor),
                              s: Store = Depends(get_store)) -> dict[str, Any]:
        return doc_step.edit_request(s, actor, request_id, body.message_text)

    @r.post("/document-requests/{request_id}/send")
    def send_document_request(request_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """The officer's approval and the send are one deliberate action. No email is delivered in this prototype."""
        return doc_step.approve_and_send(s, actor, request_id, settings)

    @r.post("/applications/{application_id}/steps/documents/complete")
    def complete_documents(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        require_role(actor, "officer")
        return steps.complete_documents(s, actor, application_for_staff(s, actor, application_id), settings)

    @r.post("/applications/{application_id}/respond")
    def applicant_reply(application_id: str, body: ReplyIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """The applicant replies to an officer's request with new files."""
        return doc_step.respond(s, actor, application_id, [d.model_dump() for d in body.documents], settings)

    @r.get("/applications/{application_id}/redaction-check")
    def redaction_check_view(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return redaction_check.overview(s, actor, application_id, settings)

    @r.get("/applications/{application_id}/redaction-check/items")
    def redaction_check_items(application_id: str, type: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        return redaction_check.group_items(s, actor, application_id, type, settings)

    @r.post("/applications/{application_id}/redaction-check/reveal")
    def redaction_reveal(application_id: str, body: RevealIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        """Show one original value on request. Audit-logged."""
        return redaction_check.reveal(s, actor, application_id, body.item_id, settings)

    @r.post("/applications/{application_id}/redaction-check/add")
    def redaction_add(application_id: str, body: AddMissedIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return redaction_check.add_missed(s, actor, application_id, body.source, body.text, body.kind, settings)

    @r.post("/applications/{application_id}/redaction-check/unmask")
    def redaction_unmask(application_id: str, body: UnmaskIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return redaction_check.unmask(s, actor, application_id, body.item_id, body.reason, settings)

    @r.post("/applications/{application_id}/redaction-check/approve",
            dependencies=[Depends(rate_limit("assess", "rate_limit_assess_per_minute"))])
    def redaction_approve(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> Any:
        """Approve the redaction check; then the AI reads the redacted version only."""
        try:
            llm = make_llm(s, settings)
        except LLMError as exc:
            return JSONResponse(status_code=503, content={"error": "llm_not_configured", "message": str(exc)})
        return redaction_check.approve(s, actor, application_id, settings, llm)

    @r.get("/applications/{application_id}/outcome")
    def get_outcome(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return outcome.build(s, actor, application_id, settings)

    @r.post("/applications/{application_id}/letter/next-steps")
    def next_steps_letter(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return letters.generate_next_steps(s, actor, application_id, settings)

    @r.post("/applications/{application_id}/reopen")
    def reopen_application(application_id: str, body: ReopenIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return review.reopen(s, actor, application_id, body.reason)

    # ---------------------------------------------------- merit marks
    @r.put("/applications/{application_id}/merit-marks/{rule_code}")
    def set_merit_mark(application_id: str, rule_code: str, body: MeritMarkIn, actor: Actor = Depends(current_actor),
                       s: Store = Depends(get_store)) -> dict[str, Any]:
        """The officer's own mark (0 to 100) with a reason, or Not assessed. Audited with the old and new value."""
        return merit.set_mark(s, actor, application_id, rule_code, mark=body.mark, not_assessed=body.not_assessed, reason=body.reason)

    # ---------------------------------------------------- reference lists
    @r.get("/reference-lists")
    def reference_lists_index(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        return reference_lists.list_all(s, actor)

    @r.get("/reference-lists/{name}")
    def reference_list_detail(name: str, q: str | None = None, actor: Actor = Depends(current_actor),
                              s: Store = Depends(get_store)) -> dict[str, Any]:
        return reference_lists.get_one(s, actor, name, q)

    @r.post("/reference-lists/{name}/preview")
    def reference_list_preview(name: str, body: ReferenceListText, actor: Actor = Depends(current_actor),
                               s: Store = Depends(get_store)) -> dict[str, Any]:
        """Read the pasted list and show what would change. Saves nothing."""
        return reference_lists.preview(s, actor, name, body.text)

    @r.put("/reference-lists/{name}")
    def reference_list_save(name: str, body: ReferenceListSave, actor: Actor = Depends(current_actor),
                            s: Store = Depends(get_store)) -> dict[str, Any]:
        """Replace the list. Audited with counts only. Re-assess applications to use the new list."""
        return reference_lists.save(s, actor, name, body.text, body.edition, body.source)

    # ------------------------------------------------------------ applicant intake
    @r.get("/programs")
    def list_programs(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        return intake.programs(s)

    @r.get("/me/applications")
    def my_applications(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> list[dict[str, Any]]:
        return intake.my_applications(s, actor)

    @r.post("/me/applications")
    def create_draft(body: DraftIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        program_id = body.grant_program_id or next((p["id"] for p in intake.programs(s)), None)
        if not program_id:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "No open grant program"})  # type: ignore[return-value]
        return intake.create_draft(s, actor, grant_program_id=program_id, fields=body.fields, answers=body.answers)

    @r.put("/me/applications/{application_id}")
    def save_draft(application_id: str, body: DraftIn, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return intake.update_draft(s, actor, application_id, fields=body.fields, answers=body.answers)

    @r.post("/me/applications/{application_id}/documents")
    def upload_document(application_id: str, body: DocumentUpload, actor: Actor = Depends(current_actor),
                        s: Store = Depends(get_store)) -> dict[str, Any]:
        return intake.add_document(s, actor, application_id, file_name=body.file_name, declared_type=body.declared_type,
                                   content_base64=body.content_base64, consistency=settings.consistency_layer)

    @r.delete("/me/applications/{application_id}/documents/{document_id}")
    def delete_document(application_id: str, document_id: str, actor: Actor = Depends(current_actor),
                        s: Store = Depends(get_store)) -> dict[str, Any]:
        return intake.remove_document(s, actor, application_id, document_id)

    @r.get("/me/applications/{application_id}/check")
    def check_draft(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return intake.check(s, actor, application_id)

    @r.post("/me/applications/{application_id}/submit")
    def submit_draft(application_id: str, body: SubmitIn | None = None, actor: Actor = Depends(current_actor),
                     s: Store = Depends(get_store)) -> dict[str, Any]:
        return intake.submit(s, actor, application_id, manual_assessment=bool(body and body.manual_assessment))

    # ------------------------------------------------------------ applicant
    @r.post("/applications/{application_id}/request-manual")
    def request_manual(application_id: str, actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        return review.request_manual_assessment(s, actor, application_id)

    # ---------------------------------------------------------------- audit
    @r.get("/audit-log", response_model=None)
    def audit_log(
        application_id: str | None = None,
        action: str | None = None,
        actor_id: str | None = None,
        since: str | None = Query(None, description="ISO timestamp"),
        until: str | None = Query(None, description="ISO timestamp"),
        limit: int = Query(500, ge=1, le=5000),
        format: str = Query("json", pattern="^(json|csv)$"),
        actor: Actor = Depends(current_actor),
        s: Store = Depends(get_store),
    ) -> Any:
        rows = audit.enrich(s, audit.list_audit(s, actor, application_id=application_id, action=action,
                                                actor_id=actor_id, since=since, until=until, limit=limit))
        if format == "csv":
            return PlainTextResponse(audit.to_csv(rows), media_type="text/csv",
                                     headers={"Content-Disposition": "attachment; filename=audit_log.csv"})
        return rows

    # ----------------------------------------------------------- evaluation
    @r.get("/evaluation/latest")
    def evaluation_latest(actor: Actor = Depends(current_actor), s: Store = Depends(get_store)) -> dict[str, Any]:
        require_role(actor, "officer", "admin")
        latest = one(s.select("evaluation_runs", order="started_at", desc=True, limit=1))
        if not latest:
            return {"evaluation_run": None, "message": "No evaluation has been run yet (python -m eval.run)"}
        return {"evaluation_run": latest}

    app.include_router(r)
    return app


def _lazy_app() -> FastAPI:
    return create_app()


app = _lazy_app()
