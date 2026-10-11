"""The shared Flag model for the consistency layer.

A flag is a signal for an officer: two pieces of evidence that do not fit
together. It is never a verdict. The model enforces the principles in code:

- the wording must be neutral (no accusation words);
- document-integrity signals (metadata, layout) are always weak;
- an AI-reported item whose quotes the code could not verify is "unclear" and
  carries no quotes;
- a flag has no score, rating or recommendation field at all.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CheckType = Literal["cross_document", "timeline", "document_integrity", "cross_application", "narrative"]
Strength = Literal["strong", "weak"]
EvidenceKind = Literal["quote", "field", "signal", "link"]

CHECK_TYPES: tuple[str, ...] = ("cross_document", "timeline", "document_integrity", "cross_application", "narrative")
TYPE_LABEL = {
    "cross_document": "Across documents",
    "timeline": "Timeline",
    "document_integrity": "Document signals",
    "cross_application": "Across applications",
    "narrative": "Statements that conflict",
}
# Words the system must never use about an applicant. Built from parts so this file itself stays clean.
_BAD = ("fr" + "aud", "fa" + "ke", "gui" + "lty", "suspi" + "cious")
FORBIDDEN = re.compile(r"(?i)\b(?:" + "|".join(_BAD) + r")\w*")

# Fields whose VALUES may be shown as evidence: dates and course facts, never identity details.
NON_PERSONAL_FIELDS = frozenset({
    "arrival_date", "course_start_date", "course_end_date", "course_name", "education_provider", "study_load",
    "declaration_date", "submitted_at", "letter_date", "visa_grant_date", "coe_issue_date", "course_duration",
    "length_of_association",
})


class Evidence(BaseModel):
    kind: EvidenceKind
    source: str = Field(description='"form", "application_text", "document:<id>", or a link label')
    label: str = Field(description="Plain words for the officer, e.g. 'Arrival date on the flight booking'")
    quote: str | None = Field(default=None, description="Verbatim passage from the REDACTED text")
    verified: bool | None = None
    field: str | None = None
    value: str | None = None  # only for NON_PERSONAL_FIELDS

    @model_validator(mode="after")
    def _check(self) -> "Evidence":
        if self.kind == "quote" and not self.quote:
            raise ValueError("a quote item needs a quote")
        if self.kind == "field" and self.value is not None and self.field not in NON_PERSONAL_FIELDS:
            raise ValueError("field values are only shown for non-personal fields")
        return self


class Flag(BaseModel):
    check_id: str                       # e.g. "cross_document.arrival_vs_start"
    type: CheckType
    strength: Strength
    description: str                    # plain English, neutral
    evidence: list[Evidence] = Field(default_factory=list)
    verification: Literal["verified", "unclear"] = "verified"
    key: str = ""                       # deterministic; set by make_key()

    @model_validator(mode="after")
    def _principles(self) -> "Flag":
        if self.type == "document_integrity" and self.strength != "weak":
            raise ValueError("document signals are always weak")
        if FORBIDDEN.search(self.description) or any(FORBIDDEN.search(e.label) for e in self.evidence):
            raise ValueError("flags describe inconsistencies; they never accuse")
        if self.verification == "unclear" and any(e.kind == "quote" for e in self.evidence):
            raise ValueError("an unverified item carries no quotes")
        if self.verification == "verified" and self.type in ("narrative", "timeline"):
            quotes = [e for e in self.evidence if e.kind == "quote"]
            if not quotes or not all(e.verified for e in quotes):
                raise ValueError("an AI-assisted flag needs verified quotes")
        return self


def make_key(check_id: str, *parts: str) -> str:
    """A stable key, so the same inconsistency found again is the same flag (and keeps the officer's decision)."""
    digest = hashlib.sha256("|".join([check_id, *parts]).encode("utf-8")).hexdigest()[:16]
    return f"{check_id}:{digest}"


def flag(check_id: str, type_: CheckType, strength: Strength, description: str, evidence: list[Evidence], *,
         key_parts: tuple[str, ...] = (), verification: Literal["verified", "unclear"] = "verified") -> Flag:
    return Flag(check_id=check_id, type=type_, strength=strength, description=description, evidence=evidence,
                verification=verification, key=make_key(check_id, *key_parts))


class ConsistencyResult(BaseModel):
    flags: list[Flag] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)   # what each check saw and returned (no raw identifiers)
