"""Redact extracted documents with the application's shared token map.

Documents are processed in document-id order (deterministic numbering), after
the form fields, so the applicant is [PERSON_1] everywhere. People inside
referee letters become [REFEREE_n]. Documents that need manual review and
have no usable text are excluded from the AI input and listed for officers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from redaction.config import RedactionConfig, default_config
from redaction.detector import Detector, TextKind
from redaction.extract import ExtractedDocument
from redaction.recognizers import KnownValue
from redaction.structured import replacements_for
from redaction.tokenizer import TokenMap


@dataclass
class RedactedDocument:
    document_id: str
    extraction_status: str
    needs_manual_review: bool
    included_in_ai_input: bool
    redacted_text: str = ""
    reason: str | None = None
    pages_without_text: list[int] = field(default_factory=list)
    metadata_keys: list[str] = field(default_factory=list)
    low_confidence: int = 0


def document_source(document_id: str) -> str:
    return f"document:{document_id}"


class DocumentRedactor:
    def __init__(self, cfg: RedactionConfig | None = None, detector: Detector | None = None) -> None:
        self.cfg = cfg or default_config()
        self.detector = detector or Detector(self.cfg)

    def redact(
        self,
        documents: list[ExtractedDocument],
        tokens: TokenMap,
        known: list[KnownValue],
        *,
        kinds: dict[str, TextKind] | None = None,
        known_addr: dict[str, str] | None = None,
    ) -> list[RedactedDocument]:
        out: list[RedactedDocument] = []
        for doc in sorted(documents, key=lambda d: d.document_id):
            rd = RedactedDocument(doc.document_id, doc.status, doc.needs_manual_review, doc.include_in_ai_input,
                                  reason=doc.reason, pages_without_text=doc.pages_without_text,
                                  metadata_keys=doc.metadata_keys)
            if doc.include_in_ai_input:
                kind = (kinds or {}).get(doc.document_id, "document")
                meta_type = "REFEREE" if kind == "referee_letter" else "PERSON"
                doc_known = known + [KnownValue(v, meta_type, f"{document_source(doc.document_id)}:metadata")
                                     for v in doc.metadata_personal_values]
                text = doc.text
                dets = self.detector.detect(text, doc_known, kind=kind)
                rd.low_confidence = sum(d.low_confidence for d in dets)
                rd.redacted_text = tokens.apply(text, replacements_for(self.cfg, text, dets, known_addr or {}),
                                                document_source(doc.document_id))
            out.append(rd)
        return out


def manual_review_list(docs: list[RedactedDocument]) -> list[dict[str, object]]:
    """Documents an officer must read themselves. Reasons only - never content."""
    return [
        {"document_id": d.document_id, "extraction_status": d.extraction_status, "reason": d.reason,
         "excluded_from_ai_input": not d.included_in_ai_input, "pages_without_text": d.pages_without_text}
        for d in docs if d.needs_manual_review
    ]
