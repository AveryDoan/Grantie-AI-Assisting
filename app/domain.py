"""Shared vocabulary and typed records used across the pipeline and services."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AIStatus = Literal["Met", "Not met", "Needs evidence", "Unclear", "Evidence only"]
DecisionStatus = Literal["Met", "Not met", "Needs evidence", "Unclear"]
Confidence = Literal["high", "medium", "low"]
RuleType = Literal["factual", "document_based", "cross_application", "judgement"]
CheckMethod = Literal["llm", "code", "human_only"]
ReviewAction = Literal["confirm", "override", "ask_applicant"]
DocType = Literal["coe", "visa", "travel_document", "other"]
Role = Literal["officer", "applicant", "admin"]

NOT_STATED = "not stated"

# Field names whose values are personal identifiers. They are tokenised before
# anything reaches the LLM and are only ever compared by plain code.
PERSONAL_FIELDS = frozenset(
    {
        "applicant_name",
        "given_names",
        "family_name",
        "contact_name",
        "contact_person",
        "date_of_birth",
        "email",
        "contact_email",
        "phone",
        "contact_phone",
        "address",
        "street_address",
        "residential_address",
        "postal_address",
        "passport_number",
        "student_id",
        "visa_grant_number",
        "coe_code",
    }
)


class Rule(BaseModel):
    id: str
    rule_pack_id: str
    rule_code: str
    rule_text: str
    source_clause: str | None = None
    source_url: str | None = None
    rule_type: RuleType
    check_method: CheckMethod
    params: dict[str, Any] = Field(default_factory=dict)
    display_order: int = 0

    @property
    def evidence_only(self) -> bool:
        """Judgement and human-only rules never get an AI status (principle 6)."""
        return self.rule_type == "judgement" or self.check_method == "human_only"


class RulePack(BaseModel):
    id: str
    grant_program_id: str
    version: str
    status: Literal["draft", "approved", "retired"]
    letter_config: dict[str, Any] = Field(default_factory=dict)
    rules: list[Rule]


class Document(BaseModel):
    id: str
    application_id: str
    file_name: str
    declared_type: str
    storage_path: str = ""
    extracted_text: str | None = None
    detected_type: DocType | None = None
    type_matches: bool | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    needs_verification: bool = False
    verification_notes: list[dict[str, Any]] = Field(default_factory=list)


class Fact(BaseModel):
    fact_key: str
    fact_value: str
    source_quote: str | None = None
    quote_verified: bool = False
    source: str | None = None


class Finding(BaseModel):
    rule_id: str
    rule_code: str
    ai_status: AIStatus
    rationale: str | None = None
    evidence_quote: str | None = None
    quote_verified: bool = False
    supporting_quotes: list[dict[str, Any]] = Field(default_factory=list)
    confidence: Confidence | None = None
    language_flag: bool = False
    needs_applicant_clarification: bool = False
    is_valid: bool = False
    error_flag: bool = False
    error_detail: str | None = None
    check_source: CheckMethod

    def enforce_invariants(self) -> Finding:
        """Apply the non-negotiable status rules in code (mirrors DB constraints).

        Called on every finding before it is stored, whatever produced it.
        """
        if self.language_flag and self.ai_status != "Unclear":
            # Principle 5: language is never a reason for "Not met".
            self.ai_status = "Unclear"
        if self.error_flag:
            # Principle 3: failure never looks like success.
            if self.ai_status != "Evidence only":
                self.ai_status = "Unclear"
            self.is_valid = False
            self.confidence = "low" if self.ai_status != "Evidence only" else None
        if self.ai_status == "Evidence only":
            self.confidence = None
        if self.evidence_quote is None:
            self.quote_verified = False
        elif not self.quote_verified:
            # Principle 2: an unverified quote is never shown as valid.
            self.is_valid = False
        if (
            self.check_source == "llm"
            and self.ai_status in ("Met", "Not met")
            and self.evidence_quote is None
        ):
            self.is_valid = False
        return self
