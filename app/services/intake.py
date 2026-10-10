"""Applicant intake: create a draft, upload documents, check, submit.

Mirrors the RLS and trigger rules (migrations 0003, 0005, 0006):
- only the applicant who owns a draft can change it, add or remove files,
  or submit it; staff can never submit on an applicant's behalf;
- a submitted application is read-only to the applicant.

Files: at most 5 MB each; PDF, plain text, or an image (headshot). PDF
metadata (author, title, XMP) is stripped before storage. Text is
extracted locally (no OCR) for the document checks; nothing here calls an
LLM. Audit entries record ids and counts, never names, values or file names.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from typing import Any

from app.services.access import Actor, application_for_applicant, require_role
from app.services.audit import write_audit
from app.services.queue import reference
from app.services.errors import Conflict, NotFound, ServiceError
from app.store.base import Store, now_iso, one

BUCKET = "application-documents"
MAX_BYTES = 5 * 1024 * 1024
CONTENT_TYPES = {"pdf": "application/pdf", "text": "text/plain", "image:png": "image/png", "image:jpeg": "image/jpeg",
                 "image:gif": "image/gif", "image:webp": "image/webp", "image:heic": "image/heic", "image:tiff": "image/tiff"}
DECLARED_TYPES = {"coe", "travel booking", "referee letter", "headshot", "biography", "transcript",
                  "offer letter", "visa", "certified translation", "other", "application responses", "resume", "certificate"}


class InvalidUpload(ServiceError):
    status_code = 422
    code = "invalid_upload"


def programs(store: Store) -> list[dict[str, Any]]:
    """Active programs an applicant can apply to (no internal fields)."""
    out = []
    for p in store.select("grant_programs", eq={"active": True}):
        if store.select("rule_packs", eq={"grant_program_id": p["id"], "status": "approved"}, limit=1):
            out.append({"id": p["id"], "name": p["name"], "description": p.get("description")})
    return sorted(out, key=lambda p: p["name"])


def _applicant_for(store: Store, actor: Actor, display_name: str | None) -> dict[str, Any]:
    row = one(store.select("applicants", eq={"user_id": actor.user_id}, limit=1))
    if row:
        return row
    profile = one(store.select("profiles", eq={"id": actor.user_id}, limit=1)) or {}
    return store.insert("applicants", {"user_id": actor.user_id,
                                       "display_name": (display_name or profile.get("display_name") or "Applicant").strip()})[0]


def _clean(values: dict[str, Any] | None) -> dict[str, str]:
    out = {}
    for k, v in (values or {}).items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k):
            raise InvalidUpload("Field names must be short snake_case identifiers")
        if v is None:
            continue
        text = str(v).strip()
        if len(text) > 8000:
            raise InvalidUpload(f"The answer for {k} is too long")
        if text:
            out[k] = text
    return out


def create_draft(store: Store, actor: Actor, *, grant_program_id: str, fields: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    require_role(actor, "applicant")
    from app.pipeline.rules_loader import load_active_pack_for_program

    program = one(store.select("grant_programs", eq={"id": grant_program_id, "active": True}, limit=1))
    if not program:
        raise NotFound("Grant program not found")
    pack = load_active_pack_for_program(store, grant_program_id)
    f, a = _clean(fields), _clean(answers)
    applicant = _applicant_for(store, actor, f.get("applicant_name"))
    app = store.insert("applications", {
        "grant_program_id": grant_program_id, "rule_pack_id": pack.id, "applicant_id": applicant["id"],
        "application_text": {"fields": f, "answers": a}, "status": "draft",
    })[0]
    write_audit(store, actor, "application.draft_created", application_id=app["id"],
                details={"fields": len(f), "answers": len(a)})
    return {"id": app["id"], "status": app["status"]}


def _draft(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    app = application_for_applicant(store, actor, application_id)
    if app["status"] != "draft":
        raise Conflict("This application has been submitted and can no longer be changed")
    return app


def update_draft(store: Store, actor: Actor, application_id: str, *, fields: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    _draft(store, actor, application_id)
    f, a = _clean(fields), _clean(answers)
    store.update("applications", {"application_text": {"fields": f, "answers": a}}, eq={"id": application_id})
    write_audit(store, actor, "application.draft_saved", application_id=application_id,
                details={"fields": len(f), "answers": len(a)})
    return {"id": application_id, "status": "draft"}


def prepare_upload(data: bytes, file_name: str, *, applicant_name: str | None = None, doc_id: str | None = None,
                   read_signals: bool = False) -> tuple[bytes, Any, dict[str, Any], str]:
    """Everything done to an uploaded file before it is stored: (bytes to store, extraction, signals, kind).

    For a PDF the metadata is read FIRST (only derived signals are kept: dates, a software class, booleans)
    and then stripped from the stored copy. The raw author, creator and producer strings are never kept.
    """
    from redaction.extract import extract_document, sniff_kind, strip_pdf_metadata

    kind = sniff_kind(data, file_name)
    doc_id = doc_id or str(uuid.uuid4())
    original = data
    if kind == "pdf":
        data = strip_pdf_metadata(data)   # the stored copy never carries author, title, XMP or any other metadata
    extracted = extract_document(doc_id, file_name, data)   # text comes from the clean copy, as it always did
    signals: dict[str, Any] = {}
    if kind == "pdf" and read_signals:
        from app.pipeline.consistency.integrity_signals import read_integrity_signals

        # Read from the ORIGINAL bytes (the stripped copy has no metadata left). Only derived signals are kept.
        signals = read_integrity_signals(original, text=extracted.text, applicant_name=applicant_name)
    return data, extracted, signals, kind


def add_document(store: Store, actor: Actor, application_id: str, *, file_name: str, declared_type: str,
                 content_base64: str, consistency: bool = False) -> dict[str, Any]:
    return store_document(store, actor, _draft(store, actor, application_id), file_name=file_name, declared_type=declared_type,
                          content_base64=content_base64, consistency=consistency)


def store_document(store: Store, actor: Actor, app: dict[str, Any], *, file_name: str, declared_type: str,
                   content_base64: str, consistency: bool = False, replaces_id: str | None = None) -> dict[str, Any]:
    """Validate, clean and store one uploaded file (shared by the draft upload and a reply to an officer's request)."""
    from redaction.extract import sniff_kind

    application_id = app["id"]
    declared = declared_type.strip().lower()
    if declared not in DECLARED_TYPES:
        raise InvalidUpload("Unknown document type")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidUpload("The file could not be read") from exc
    if not data:
        raise InvalidUpload("The file is empty")
    if len(data) > MAX_BYTES:
        raise InvalidUpload("File too large. Choose a file smaller than 5 MB or ask a person for help.")
    safe_name = re.sub(r"[^\w.\- ]", "_", file_name.strip())[:120] or "document"
    if sniff_kind(data, safe_name) not in CONTENT_TYPES:
        raise InvalidUpload("Upload a PDF, a plain-text file, or a JPG/PNG photo. Word files are not supported yet.")
    doc_id = str(uuid.uuid4())
    typed_name = ((app.get("application_text") or {}).get("fields") or {}).get("applicant_name")
    # Metadata is read before it is stripped (author/title/XMP often hold names); only derived signals are kept.
    data, extracted, signals, kind = prepare_upload(data, safe_name, applicant_name=typed_name, doc_id=doc_id, read_signals=consistency)
    path = f"{application_id}/{doc_id}"  # no file name in the path: names often contain personal details
    upload = getattr(store, "upload", None)
    if upload is None:
        raise ServiceError("File storage is not available")
    upload(BUCKET, path, data, CONTENT_TYPES[kind])
    from app.pipeline.documents import classify

    values = {
        "id": doc_id, "application_id": application_id, "storage_path": path, "file_name": safe_name,
        "declared_type": declared, "extracted_text": extracted.text or None,
        "detected_type": None, "is_sample": True,
    }
    if replaces_id:
        values["replaces_id"] = replaces_id   # column added by migration 0015
    if kind == "pdf":
        from redaction.layout import extract_layout

        values["pdf_layout"] = extract_layout(extracted, data)   # word boxes for highlighting and blur (migration 0016)
    if consistency:
        values["integrity_signals"] = signals   # column added by migration 0011
    row = store.insert("documents", values)[0]
    looks_like = classify(extracted.text) if extracted.text else None
    write_audit(store, actor, "document.uploaded", application_id=application_id,
                details={"document_id": doc_id, "declared_type": declared, "kind": kind, "bytes": len(data),
                         "extraction_status": extracted.status})
    return {
        "id": row["id"], "file_name": safe_name, "declared_type": declared, "kind": kind,
        "extraction_status": extracted.status, "needs_manual_review": extracted.needs_manual_review,
        "pages": len(extracted.pages), "looks_like": looks_like,
    }


def remove_document(store: Store, actor: Actor, application_id: str, document_id: str) -> dict[str, Any]:
    _draft(store, actor, application_id)
    doc = one(store.select("documents", eq={"id": document_id, "application_id": application_id}, limit=1))
    if not doc:
        raise NotFound("Document not found")
    remove = getattr(store, "remove", None)
    if remove:
        remove(BUCKET, doc["storage_path"])
    store.delete("documents", eq={"id": document_id})
    write_audit(store, actor, "document.removed", application_id=application_id, details={"document_id": document_id})
    return {"removed": document_id}


def check(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    """The pre-submission check on the stored draft (missing items only; never eligibility)."""
    from app.services.precheck import precheck

    app = application_for_applicant(store, actor, application_id)
    docs = store.select("documents", eq={"application_id": application_id})
    return precheck(store, actor, grant_program_id=app["grant_program_id"],
                    fields={**(app["application_text"].get("fields") or {}), **(app["application_text"].get("answers") or {})},
                    documents=[{"declared_type": d["declared_type"], "file_name": d["file_name"],
                                "extracted_text": d.get("extracted_text") or ""} for d in docs])


def submit(store: Store, actor: Actor, application_id: str, *, manual_assessment: bool = False) -> dict[str, Any]:
    _draft(store, actor, application_id)
    values: dict[str, Any] = {"status": "submitted", "submitted_at": now_iso()}
    if manual_assessment:
        values["manual_assessment_requested"] = True
    app = store.update("applications", values, eq={"id": application_id})[0]
    docs = store.select("documents", eq={"application_id": application_id})
    write_audit(store, actor, "application.submitted", application_id=application_id,
                details={"documents": len(docs), "manual_assessment_requested": bool(app.get("manual_assessment_requested"))})
    return {"id": application_id, "status": "submitted", "submitted_at": app["submitted_at"],
            "reference": reference(application_id),
            "manual_assessment_requested": bool(app.get("manual_assessment_requested"))}


def my_applications(store: Store, actor: Actor) -> list[dict[str, Any]]:
    require_role(actor, "applicant")
    applicant = one(store.select("applicants", eq={"user_id": actor.user_id}, limit=1))
    if not applicant:
        return []
    rows = store.select("applications", eq={"applicant_id": applicant["id"]}, order="created_at", desc=True)
    return [{"id": a["id"], "status": a["status"], "submitted_at": a.get("submitted_at"),
             "grant_program_id": a["grant_program_id"], "reference": reference(a["id"])} for a in rows]
