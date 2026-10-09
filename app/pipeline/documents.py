"""Pipeline step 4: document checks (plain code; documents never go to the LLM).

a) required document present?  b) right type? (keyword classifier)
c) extract fields from "Label: value" lines of the SAMPLE document text
d) compare with the values typed on the form, tolerating date formats, name
   order, diacritics and minor transliteration differences.

A mismatch sets `needs_verification`. It is never labelled fraud and never
causes an automatic rejection. Scanned or photographed documents are out of
scope: only text sample documents are supported.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.domain import DocType
from app.pipeline.parsing import date_candidates, parse_date

_TYPE_KEYWORDS: dict[DocType, list[str]] = {
    "coe": ["confirmation of enrolment", "confirmation of enrollment", "coe code", "cricos", "course start date"],
    "offer_letter": ["letter of offer", "offer of a place", "pleased to offer", "entry requirements", "offer letter"],
    "visa": ["visa grant", "visa subclass", "grant notice", "visa expiry", "must not arrive after", "stay until"],
    "travel_document": ["passport", "travel document", "nationality", "place of birth", "document number"],
    "travel_booking": ["booking reference", "itinerary", "e-ticket", "passenger", "flight number", "booking confirmation"],
    # Not the bare word "referee": it appears in a résumé's "Referees" section and in many footers.
    "referee_letter": ["to whom it may concern", "referee name", "i have known", "letter of reference", "reference letter",
                       "letter of support", "length of association", "yours sincerely", "i recommend"],
    "headshot": ["headshot", "photograph"],
}
# Checked first: a screenshot of a flight search is not a booking, and a
# translation of a letter is a translation, whatever else it mentions.
_PRIORITY_TYPES: list[tuple[DocType, list[str]]] = [
    ("certified_translation", ["certified translation", "naati", "true and accurate translation"]),
    ("flight_screenshot", ["screenshot", "search results", "select your flight"]),
]
_ENGLISH_WORDS = frozenset(
    "the and of to in is for this that with you i we a an are was be have has on at as by from it my our your".split()
)

_DECLARED_ALIASES: dict[str, DocType] = {
    "coe": "coe",
    "confirmation of enrolment": "coe",
    "enrolment": "coe",
    "visa": "visa",
    "visa grant notice": "visa",
    "passport": "travel_document",
    "travel document": "travel_document",
    "travel_document": "travel_document",
    "offer letter": "offer_letter",
    "offer_letter": "offer_letter",
    "letter of offer": "offer_letter",
    "booking": "travel_booking",
    "travel booking": "travel_booking",
    "travel_booking": "travel_booking",
    "itinerary": "travel_booking",
    "flight booking": "travel_booking",
    "referee letter": "referee_letter",
    "referee_letter": "referee_letter",
    "reference letter": "referee_letter",
    "headshot": "headshot",
    "photo": "headshot",
    "translation": "certified_translation",
    "certified translation": "certified_translation",
    "certified_translation": "certified_translation",
}

_FIELD_ALIASES: dict[str, str] = {
    "student name": "full_name",
    "name": "full_name",
    "full name": "full_name",
    "visa holder": "full_name",
    "holder name": "full_name",
    "given names": "given_names",
    "given name": "given_names",
    "family name": "family_name",
    "surname": "family_name",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "passport number": "passport_number",
    "document number": "passport_number",
    "passport no": "passport_number",
    "course": "course_name",
    "course name": "course_name",
    "provider": "provider_name",
    "education provider": "provider_name",
    "course start date": "course_start_date",
    "start date": "course_start_date",
    "course end date": "course_end_date",
    "end date": "course_end_date",
    "expected completion": "course_end_date",
    "visa subclass": "visa_subclass",
    "grant date": "visa_grant_date",
    "date of grant": "visa_grant_date",
    "visa expiry": "visa_expiry_date",
    "visa expiry date": "visa_expiry_date",
    "stay until": "visa_expiry_date",
    "date of expiry": "document_expiry_date",
    "expiry date": "document_expiry_date",
    "nationality": "nationality",
    "study load": "study_load",
    "mode of study": "study_load",
    "date coe issued": "coe_issue_date",
    "coe issue date": "coe_issue_date",
    "course duration": "course_duration",
    "attendance": "study_load",
    "booking reference": "booking_reference",
    "arrival": "arrival_date",
    "arrival date": "arrival_date",
    "arrives": "arrival_date",
}

DOC_LABEL_SHORT: dict[str, str] = {
    "coe": "a Confirmation of Enrolment (CoE)",
    "visa": "a visa grant notice",
    "travel_document": "a passport or travel document",
    "offer_letter": "a letter of offer",
    "travel_booking": "a travel booking or itinerary",
    "flight_screenshot": "a screenshot of a flight (not a booking)",
    "referee_letter": "a referee letter",
    "headshot": "a headshot photo",
    "certified_translation": "a certified translation",
    "other": "another kind of document",
}

DATE_FIELDS = {
    "arrival_date",
    "date_of_birth",
    "course_start_date",
    "course_end_date",
    "visa_grant_date",
    "visa_expiry_date",
    "document_expiry_date",
    "coe_issue_date",
}

# typed form field -> document field to compare against
COMPARISONS: list[tuple[str, str, str]] = [
    ("applicant_name", "full_name", "name"),
    ("date_of_birth", "date_of_birth", "date"),
    ("passport_number", "passport_number", "id"),
    ("course_name", "course_name", "text"),
]


def normalise_declared(declared: str) -> DocType:
    return _DECLARED_ALIASES.get(declared.strip().lower(), "other")


def detect_type(text: str, declared: DocType) -> DocType:
    """A photo has no text to classify: take a declared headshot at its word (the officer
    checks the photo by eye). Every other document must show its type in its text."""
    if not text.strip() and declared == "headshot":
        return "headshot"
    return classify(text)


def classify(text: str) -> DocType:
    lowered = text.lower()
    for doc_type, keywords in _PRIORITY_TYPES:
        if any(kw in lowered for kw in keywords):
            return doc_type
    scores = {t: sum(1 for kw in kws if kw in lowered) for t, kws in _TYPE_KEYWORDS.items()}
    best = max(scores, key=lambda t: scores[t])
    return best if scores[best] > 0 else "other"


def is_english(text: str, *, min_words: int = 20, threshold: float = 0.06) -> bool | None:
    """Rough check that a text is in English. Code-only; an officer verifies translations.

    - many words with accented or non-Latin letters -> not English;
    - otherwise English if common English words are frequent enough, or if the
      text is mostly "Label: value" lines (forms, bookings, CoEs);
    - None when there is too little text to tell.
    """
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < min_words:
        return None
    if sum(any(ord(ch) > 127 for ch in w) for w in words) / len(words) > 0.2:
        return False
    if sum(w in _ENGLISH_WORDS for w in words) / len(words) >= threshold:
        return True
    labelled = sum(1 for line in text.splitlines() if re.match(r"^\s*[A-Za-z][A-Za-z ()/-]{1,40}:\s*\S", line))
    return labelled >= 3


# Labels that may also appear WITHOUT a colon ("Course start date 22/02/2027"). Only labels that
# cannot be ordinary words; "course" alone needs a qualification word after it.
_COLONLESS = sorted({a for a in _FIELD_ALIASES if " " in a or a in ("surname", "nationality")}, key=len, reverse=True)
_QUALIFICATION = re.compile(r"(?i)^(master|bachelor|diploma|certificate|doctor|graduate|associate|advanced)\b")
_LETTERHEAD = re.compile(r"^(?:[A-Z][\w'’&.-]*\s+){0,6}(?:University|College|Institute)(?:\s+(?:of|for|and|[A-Z][\w'’&.-]*)){0,4}$")


def _split_label(line: str) -> tuple[str, str] | None:
    """(label, value) from 'Label: value' or, for safe labels, 'Label value'."""
    if ":" in line:
        label, _, value = line.partition(":")
        return label, value
    norm = re.sub(r"\s+", " ", line.strip())
    low = norm.lower()
    for alias in _COLONLESS:
        if low.startswith(alias + " "):
            return alias, norm[len(alias) + 1:]
    if low.startswith("course ") and _QUALIFICATION.match(norm[7:]):
        return "course", norm[7:]
    return None


def extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        parts = _split_label(line)
        if not parts:
            continue
        label, value = parts
        norm_label = re.sub(r"\s+", " ", label.strip().lower())
        key = _FIELD_ALIASES.get(norm_label) or ("arrival_date" if norm_label.startswith("arrival in ") else None)
        if key and value.strip() and key not in fields:
            value = value.strip()
            if key == "arrival_date":  # e.g. "Darwin (DRW) 20 September 2026 14:05"
                m = re.search(r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", value)
                value = m.group(1) if m else value
            fields[key] = value
    if "provider_name" not in fields and "course_name" in fields:
        # A CoE often has no "Provider:" label: the letterhead (one of the first lines) names the provider.
        head = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("[page")][:4]
        letterhead = next((l for l in head if _LETTERHEAD.match(l)), None)
        if letterhead:
            fields["provider_name"] = letterhead
    if "full_name" not in fields and ("given_names" in fields or "family_name" in fields):
        fields["full_name"] = " ".join(x for x in (fields.get("given_names"), fields.get("family_name")) if x)
    for k in DATE_FIELDS & fields.keys():
        d = parse_date(fields[k])
        if d:
            fields[f"{k}_iso"] = d.isoformat()
    return fields


def compare_names(typed: str, document: str, threshold: float) -> tuple[str, float]:
    """match | variant | mismatch. Order-insensitive, diacritic-insensitive."""

    def tokens(s: str) -> list[str]:
        s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
        s = s.replace("đ", "d").replace("Đ", "D")
        return re.sub(r"[^a-z ]", " ", s.lower()).split()

    a, b = tokens(typed), tokens(document)
    if sorted(a) == sorted(b):
        return "match", 100.0
    score = fuzz.token_sort_ratio(" ".join(a), " ".join(b))
    if score >= threshold:
        return "variant", score  # e.g. Mohammed / Muhammad
    return "mismatch", score


def compare_value(kind: str, typed: str, document: str, name_threshold: float) -> tuple[str, float | None]:
    if kind == "name":
        return compare_names(typed, document, name_threshold)
    if kind == "date":
        t, d = date_candidates(typed), parse_date(document)
        if d is None or not t:
            return "unparseable", None
        if parse_date(typed) == d:
            return "match", None
        return ("variant", None) if d in t else ("mismatch", None)
    if kind == "id":
        clean = lambda s: re.sub(r"[\s-]", "", s).upper()  # noqa: E731
        return ("match", None) if clean(typed) == clean(document) else ("mismatch", None)
    score = fuzz.token_set_ratio(typed.lower(), document.lower())
    return ("match" if score >= 90 else "variant" if score >= 75 else "mismatch"), score


@dataclass
class DocumentCheck:
    document_id: str
    declared_type: DocType
    detected_type: DocType
    type_matches: bool
    extracted_fields: dict[str, Any]
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def needs_verification(self) -> bool:
        return any(c["result"] in ("mismatch", "unparseable") for c in self.comparisons)


@dataclass
class DocumentChecks:
    checks: list[DocumentCheck]

    def present_types(self) -> set[DocType]:
        return {c.detected_type for c in self.checks}

    def by_type(self, doc_type: DocType) -> list[DocumentCheck]:
        return [c for c in self.checks if c.detected_type == doc_type]

    def comparisons(self) -> list[dict[str, Any]]:
        return [c | {"document_id": chk.document_id} for chk in self.checks for c in chk.comparisons]


def check_documents(
    typed_fields: dict[str, Any], documents: list[dict[str, Any]], *, name_threshold: float = 85.0
) -> DocumentChecks:
    checks: list[DocumentCheck] = []
    for doc in documents:
        text = doc.get("extracted_text") or ""
        declared = normalise_declared(doc.get("declared_type") or "")
        detected = detect_type(text, declared)
        fields_ = extract_fields(text)
        chk = DocumentCheck(
            document_id=doc["id"],
            declared_type=declared,
            detected_type=detected,
            type_matches=declared == detected,
            extracted_fields=fields_,
        )
        for typed_key, doc_key, kind in COMPARISONS:
            typed_val = typed_fields.get(typed_key)
            doc_val = fields_.get(doc_key)
            if not typed_val or not doc_val:
                continue
            result, score = compare_value(kind, str(typed_val), str(doc_val), name_threshold)
            chk.comparisons.append(
                {"field": typed_key, "result": result, "similarity": score, "label": "needs verification" if result in ("mismatch", "unparseable") else result}
            )
        checks.append(chk)
    return DocumentChecks(checks)
