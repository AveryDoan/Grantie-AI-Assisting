"""Request bodies. Response bodies never contain an eligibility score, a
ranking, or an approve/reject recommendation (see tests/test_api.py)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt

from app.domain import DecisionStatus


class AssessRequest(BaseModel):
    force: bool = Field(False, description="Re-run even if inputs are unchanged (creates a new run)")
    consistency_check: bool | None = Field(None, description="Override the CONSISTENCY_CHECK setting")


class ReviewRequest(BaseModel):
    action: Literal["confirm", "override", "ask_applicant"]
    final_status: DecisionStatus | None = None
    reason: str | None = Field(None, max_length=4000)


class ClarificationRequestIn(BaseModel):
    finding_id: str | None = None
    message_text: str | None = Field(None, max_length=4000)
    send: bool = Field(False, description="Mock send: marks as sent, no email is delivered")
    clarification_id: str | None = Field(None, description="Send/edit an existing draft instead of creating one")


class SignOffRequest(BaseModel):
    statement_acknowledged: bool


class LetterPatch(BaseModel):
    body_text: str | None = Field(None, max_length=50000)
    approve: bool = False


class PrecheckDocument(BaseModel):
    declared_type: str
    file_name: str | None = None
    text: str = Field("", max_length=50000, description="Text of a SAMPLE document (synthetic only)")


class PrecheckRequest(BaseModel):
    grant_program_id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    documents: list[PrecheckDocument] = Field(default_factory=list)


class DemoLogin(BaseModel):
    """Demo mode only (APP_MODE=demo)."""

    role: Literal["officer", "admin", "applicant", "sita"] = "officer"


class RedactRequest(BaseModel):
    force: bool = Field(False, description="Re-run even if nothing changed")


class DraftIn(BaseModel):
    """Applicant draft. Field names are snake_case; values are plain text."""

    grant_program_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    answers: dict[str, Any] = Field(default_factory=dict)


class DocumentUpload(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    declared_type: str = Field(min_length=1, max_length=40)
    content_base64: str = Field(min_length=1, max_length=7_100_000)  # 5 MB file, base64-encoded


class FlagReview(BaseModel):
    action: Literal["confirm", "dismiss"]
    note: str | None = Field(default=None, max_length=2000)


class SubmitIn(BaseModel):
    manual_assessment: bool = False


class ReferenceListText(BaseModel):
    text: str = Field(..., max_length=400_000, description="One row per line: OSCA code, occupation, skill level")


class ReferenceListSave(ReferenceListText):
    edition: str | None = Field(None, max_length=80, description="For example: 31 August 2026")
    source: str | None = Field(None, max_length=300)


class MeritMarkIn(BaseModel):
    """The officer's own mark. There is no AI-suggested value and no default."""

    mark: StrictInt | None = Field(None, description="Whole number from 0 to 100, or null with not_assessed")
    not_assessed: bool = False
    reason: str | None = Field(None, max_length=2000)


class DocumentDecisionIn(BaseModel):
    slot: str
    decision: Literal["confirmed", "wrong_slot", "request_again", "not_needed"]
    reason: str | None = Field(None, max_length=1000)


class RequestItemIn(BaseModel):
    slot: str
    kind: Literal["missing", "wrong_slot", "unreadable", "differs", "other"] = "other"
    reason: str | None = Field(None, max_length=500)
    note: str | None = Field(None, max_length=500)


class RequestDraftIn(BaseModel):
    items: list[RequestItemIn] = Field(..., min_length=1, max_length=8)


class RequestEditIn(BaseModel):
    message_text: str = Field(..., max_length=6000)


class RevealIn(BaseModel):
    item_id: str = Field(..., max_length=200)


class AddMissedIn(BaseModel):
    source: str = Field(..., max_length=200)
    text: str = Field(..., max_length=300)
    kind: str = Field(..., max_length=40)


class UnmaskIn(BaseModel):
    item_id: str = Field(..., max_length=200)
    reason: str | None = Field(None, max_length=1000)


class ReopenIn(BaseModel):
    reason: str | None = Field(None, max_length=2000)


class ReplyIn(BaseModel):
    documents: list[DocumentUpload] = Field(..., min_length=1, max_length=8)


class LocateItem(BaseModel):
    id: str = Field(..., max_length=200)
    start: int | None = None
    end: int | None = None
    text: str | None = Field(None, max_length=2000)


class LocateIn(BaseModel):
    items: list[LocateItem] = Field(..., max_length=200)


class EvidenceDraftIn(BaseModel):
    finding_ids: list[str] | None = None
