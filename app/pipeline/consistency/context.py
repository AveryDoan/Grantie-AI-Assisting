"""What the consistency checks are given.

Text checks run on the REDACTED text (so every quote they store can be found
again in the redacted copy and restored for the officer). The applicant's
typed date of birth is used by code only, locally, and never shown or sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.pipeline.documents import extract_fields, normalise_declared

LABELS = {
    "coe": "Confirmation of Enrolment", "visa": "Visa grant notice", "travel_booking": "Flight booking",
    "offer_letter": "Letter of offer", "referee_letter": "Referee letter", "headshot": "Headshot",
    "travel_document": "Passport or travel document", "certified_translation": "Certified translation",
    "other": "Supporting document",
}


@dataclass
class DocView:
    id: str
    type: str                    # detected type if known, else declared (normalised)
    label: str
    text: str = ""               # REDACTED text ("" when the document never reaches the AI)
    original: str = field(default="", repr=False)   # original text: used by code only (hashing identifiers), never stored or shown
    fields: dict[str, str] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    declared: str = "other"

    @property
    def source(self) -> str:
        return f"document:{self.id}"


@dataclass
class ConsistencyContext:
    app_id: str
    fields: dict[str, str]                  # typed form fields (original values; code use only)
    answers: dict[str, str]
    submitted: date
    docs: list[DocView] = field(default_factory=list)
    referees: list[Any] = field(default_factory=list)       # RefereeLetter objects (verified AI extraction)
    form_text: str = ""                     # REDACTED application text
    applicant_token: str | None = None      # the placeholder standing for the applicant's name
    token_map: Any = None
    guard: Any = None                       # GuardedLLM, or None (LLM checks are skipped)
    store: Any = None
    applicant_id: str | None = None
    hash_key: str | None = None
    letters_need_marks: bool = False        # does the rule pack require signature and letterhead?
    fuzzy_threshold: float = 92.0
    name_threshold: float = 85.0

    def of_type(self, *types: str) -> list[DocView]:
        return [d for d in self.docs if d.type in types]

    def first(self, type_: str) -> DocView | None:
        return next((d for d in self.docs if d.type == type_ and d.text), None)

    def letters(self) -> list[DocView]:
        return [d for d in self.docs if d.type == "referee_letter" and d.text]

    def referee_for(self, doc_id: str) -> Any | None:
        return next((r for r in self.referees if r.document_id == doc_id), None)


def doc_label(type_: str, index: int | None = None) -> str:
    base = LABELS.get(type_, LABELS["other"])
    return f"{base} {index}" if index else base


def build_docs(rows: list[dict[str, Any]], detected: dict[str, str], redacted: dict[str, str],
               originals: dict[str, str] | None = None) -> list[DocView]:
    """DocViews from document rows. `redacted`: document id -> redacted text (only for AI-readable documents)."""
    out: list[DocView] = []
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    for r in rows:
        t = detected.get(r["id"]) or normalise_declared(r.get("declared_type") or "")
        totals[t] = totals.get(t, 0) + 1
    for r in rows:
        declared = normalise_declared(r.get("declared_type") or "")
        t = detected.get(r["id"]) or declared
        counts[t] = counts.get(t, 0) + 1
        text = redacted.get(r["id"], "")
        out.append(DocView(
            id=r["id"], type=t, declared=declared, text=text, original=(originals or {}).get(r["id"], ""),
            fields=extract_fields(text) if text else {},
            signals=r.get("integrity_signals") or {}, label=doc_label(t, counts[t] if totals[t] > 1 else None)))
    return out
