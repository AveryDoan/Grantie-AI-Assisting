"""Pipeline step 2: redaction.

Before anything reaches the LLM, personal identifiers are replaced by tokens
such as [PERSON_1], [EMAIL_1], [PHONE_1], [ADDRESS_1], [ID_1]. Organisation-
level facts the rules need (entity type, region, dates, amounts, ABN,
organisation names) are kept.

The token -> original mapping is stored (staff-only) so the officer sees the
full record and letters can quote the applicant's own words.

Limits: pattern-based. Names are caught from personal form fields, honorifics
("Ms Jane Citizen") and introductions ("my name is ..."). Free-text names
without those cues can slip through; the README lists this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain import PERSONAL_FIELDS

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Australian mobiles/landlines and international numbers.
PHONE = re.compile(r"(?<!\w)(?:\+?61[ -]?|0)[2-478](?:[ -]?\d){8}(?!\d)|(?<!\w)\+\d{1,3}(?:[ -]?\d){7,12}(?!\d)")
STREET_TYPES = (
    r"Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Court|Ct|Crescent|Cres|Place|Pl|Way|Terrace|Tce|"
    r"Highway|Hwy|Parade|Pde|Boulevard|Blvd|Circuit|Cct|Close|Cl"
)
ADDRESS = re.compile(
    rf"\b(?:(?:Unit|Apt|Apartment|Flat)\s*\d+[A-Za-z]?[,/ ]\s*)?\d{{1,5}}[A-Za-z]?\s+(?:[A-Z][a-z]+\s){{1,3}}(?:{STREET_TYPES})\b\.?",
)
PO_BOX = re.compile(r"\b(?:PO|P\.O\.)\s*Box\s*\d+\b", re.IGNORECASE)
# Passport-like (1-2 letters + 6-9 digits), labelled IDs, long digit runs.
ID_LABELLED = re.compile(
    r"\b(?:passport|student id|student number|visa grant number|grant number|coe(?: code)?|licen[cs]e)"
    r"(?:\s*(?:no\.?|number|#))?\s*[:\-]?\s*([A-Z0-9][A-Z0-9-]{4,})",
    re.IGNORECASE,
)
ID_SHAPE = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
LONG_DIGITS = re.compile(r"(?<![\d$.,])\d{9,}(?![\d.,])")
ABN_CONTEXT = re.compile(r"\bABN\b", re.IGNORECASE)
HONORIFIC_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+((?:[A-Z][a-zA-Z'-]+\s?){1,3})")
INTRO_NAME = re.compile(r"\b(?:[Mm]y name is|I am called|I'm called)\s+((?:[A-Z][a-zA-Z'-]+\s?){1,3})")


@dataclass
class RedactionResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # token -> original

    def restore(self, text: str) -> str:
        """Put original values back (for officer views and letters only)."""
        for token, original in self.mapping.items():
            text = text.replace(token, original)
        return text


class _Redactor:
    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self._reverse: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def token(self, kind: str, value: str) -> str:
        key = (kind, value.strip())
        if key not in self._reverse:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            tok = f"[{kind}_{self._counts[kind]}]"
            self._reverse[key] = tok
            self.mapping[tok] = value.strip()
        return self._reverse[key]

    def sub(self, pattern: re.Pattern[str], kind: str, text: str, group: int = 0) -> str:
        def repl(m: re.Match[str]) -> str:
            if kind == "ID" and ABN_CONTEXT.search(text[max(0, m.start() - 12) : m.start()]):
                return m.group(0)  # ABN is an organisation-level fact
            value = m.group(group)
            if group:
                return m.group(0).replace(value, self.token(kind, value))
            return self.token(kind, value)

        return pattern.sub(repl, text)


def _render(application_text: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten {"fields": {...}, "answers": {...}} into (label, value) lines."""
    lines: list[tuple[str, str]] = []
    for section in ("fields", "answers"):
        for key, value in (application_text.get(section) or {}).items():
            if value is None or value == "":
                continue
            lines.append((key, str(value)))
    for key, value in application_text.items():
        if key not in ("fields", "answers") and isinstance(value, (str, int, float)):
            lines.append((key, str(value)))
    return lines


def render_source_text(application_text: dict[str, Any]) -> str:
    """Unredacted plain-text rendering (officer view; letter quote checks)."""
    return "\n".join(f"{k}: {v}" for k, v in _render(application_text))


def redact(application_text: dict[str, Any], extra_names: list[str] | None = None) -> RedactionResult:
    r = _Redactor()
    lines = _render(application_text)

    # 1. Whole personal fields become tokens; remember names to scrub elsewhere.
    names: list[str] = [n for n in (extra_names or []) if n and n.strip()]
    out: list[tuple[str, str]] = []
    for key, value in lines:
        if key in PERSONAL_FIELDS:
            kind = (
                "PERSON"
                if "name" in key or key == "contact_person"
                else "EMAIL"
                if "email" in key
                else "PHONE"
                if "phone" in key
                else "ADDRESS"
                if "address" in key
                else "DOB"
                if key == "date_of_birth"
                else "ID"
            )
            if kind == "PERSON":
                names.append(value)
            out.append((key, r.token(kind, value)))
        else:
            out.append((key, value))

    # Individual name parts too (e.g. "Thanh" alone), longest first.
    name_parts = set()
    for n in names:
        name_parts.add(n.strip())
        name_parts.update(p for p in re.split(r"[\s,]+", n) if len(p) >= 3)

    redacted: list[str] = []
    for key, value in out:
        text = value
        # Longest first, so a full name is replaced before its parts.
        for part in sorted(name_parts, key=len, reverse=True):
            tok = r.token("PERSON", part)
            text = re.sub(rf"(?<![\[\w]){re.escape(part)}\b", lambda _m, t=tok: t, text)
        text = r.sub(EMAIL, "EMAIL", text)
        text = r.sub(PHONE, "PHONE", text)
        text = r.sub(ADDRESS, "ADDRESS", text)
        text = r.sub(PO_BOX, "ADDRESS", text)
        text = r.sub(ID_LABELLED, "ID", text, group=1)
        text = r.sub(ID_SHAPE, "ID", text)
        text = r.sub(LONG_DIGITS, "ID", text)
        text = r.sub(HONORIFIC_NAME, "PERSON", text, group=1)
        text = r.sub(INTRO_NAME, "PERSON", text, group=1)
        redacted.append(f"{key}: {text}")

    return RedactionResult(text="\n".join(redacted), mapping=r.mapping)


LABELLED_NAME = re.compile(
    r"^(?P<label>\s*(?:referee name|name|signed|signature|from|student name|applicant name)\s*:\s*)(?P<value>[^\n\[]+)$",
    re.IGNORECASE | re.MULTILINE,
)


def redact_text(text: str, extra_names: list[str] | None = None) -> RedactionResult:
    """Redact free document text (e.g. a referee letter) before an LLM sees it.

    Same patterns as `redact`, plus "Name: ..." style lines. Signature
    placeholders such as "[signed]" are kept so presence can be checked.
    """
    r = _Redactor()
    for part in sorted({n.strip() for n in (extra_names or []) if n and n.strip()}, key=len, reverse=True):
        text = re.sub(rf"(?<![\[\w]){re.escape(part)}\b", lambda _m, t=r.token("PERSON", part): t, text)
    text = LABELLED_NAME.sub(lambda m: m.group("label") + r.token("PERSON", m.group("value").strip()), text)
    for pattern, kind, group in (
        (EMAIL, "EMAIL", 0), (PHONE, "PHONE", 0), (ADDRESS, "ADDRESS", 0), (PO_BOX, "ADDRESS", 0),
        (ID_LABELLED, "ID", 1), (ID_SHAPE, "ID", 0), (LONG_DIGITS, "ID", 0),
        (HONORIFIC_NAME, "PERSON", 1), (INTRO_NAME, "PERSON", 1),
    ):
        text = r.sub(pattern, kind, text, group=group)
    return RedactionResult(text=text, mapping=r.mapping)
