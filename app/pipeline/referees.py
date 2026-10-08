"""Referee letter extraction (Study NT rules D4, D5 and merit criterion M2).

Each referee letter is redacted, wrapped in delimiters as DATA, and the LLM
extracts plain fields with exact quotes. Code then verifies every quote
against the redacted letter and decides completeness and dates (D4, D5).
Signature and letterhead are images on a real letter: text can only show a
mention, so the officer checks them by eye.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.domain import NOT_STATED, Fact
from app.llm import LLMClient, LLMError
from app.llm.base import Prompt
from app.pipeline.injection import neutralise_delimiters
from app.pipeline.prompts import PROMPT_VERSION, _DATA_RULES
from app.pipeline.redaction import redact_text
from app.pipeline.verification import verify_quote

REFEREE_FIELDS = [
    "referee_name",
    "position",
    "organisation",
    "relationship",
    "length_of_association",
    "letter_date",
    "signature",
    "letterhead",
]
# Fields D4 requires as text. Signature and letterhead are checked by the officer.
REQUIRED_TEXT_FIELDS = REFEREE_FIELDS[:6]


class RefereeItem(BaseModel):
    key: str = Field(description="One of the requested field keys")
    value: str = Field(description='The value as written, or exactly "not stated"')
    quote: str | None = Field(description="Exact verbatim words from the letter, or null if not stated")


class RefereeLetterOut(BaseModel):
    fields: list[RefereeItem]
    highlights: list[str] = Field(description="Up to 3 verbatim sentences describing the applicant; may be empty")


SYSTEM_REFEREE = f"""\
You extract plain facts from ONE referee letter for a human grants officer.

{_DATA_RULES.replace("<application_data>", "<document_data>").replace("</application_data>", "</document_data>")}

For each requested field key, give the value exactly as written in the letter and an exact verbatim quote
containing it. If the letter does not state it, the value must be exactly "not stated" and quote null.
For "signature", report whether the text shows a signature line or a mark such as "[signed]".
For "letterhead", report whether the text shows an organisation letterhead.
Also return up to 3 verbatim sentences in which the referee describes the applicant.
Never guess, judge, score or summarise. Return only the JSON object."""


@dataclass
class RefereeLetter:
    document_id: str
    fields: dict[str, Fact] = field(default_factory=dict)
    highlights: list[dict[str, Any]] = field(default_factory=list)
    redacted_text: str = ""
    mapping: dict[str, str] = field(default_factory=dict)  # token -> original, for the officer's view only
    error: str | None = None

    def restore(self, text: str | None) -> str | None:
        if text is None:
            return None
        for token, original in self.mapping.items():
            text = text.replace(token, original)
        return text

    def stated(self, key: str) -> bool:
        f = self.fields.get(key)
        return bool(f and f.fact_value != NOT_STATED and f.quote_verified)


def referee_prompt(redacted: str, rule_pack_version: str) -> Prompt:
    data = f"<document_data>\n{neutralise_delimiters(redacted)}\n</document_data>"
    return Prompt(
        task="referee",
        system=SYSTEM_REFEREE,
        user="Field keys: " + ", ".join(REFEREE_FIELDS) + "\n\n" + data,
        prompt_version=PROMPT_VERSION,
        cache_text=redacted,
        rule_pack_version=rule_pack_version,
        meta={"kind": "referee", "source": redacted},
    )


def extract_referee_letters(
    llm: LLMClient | None,
    letters: list[dict[str, Any]],
    rule_pack_version: str,
    *,
    applicant_names: list[str] | None = None,
    threshold: float = 92.0,
) -> list[RefereeLetter]:
    out: list[RefereeLetter] = []
    for doc in letters:
        red = redact_text(doc.get("extracted_text") or "", applicant_names)
        letter = RefereeLetter(document_id=doc["id"], redacted_text=red.text, mapping=red.mapping)
        if llm is None:
            letter.error = "No LLM configured"
            out.append(letter)
            continue
        try:
            result = llm.call_llm(referee_prompt(red.text, rule_pack_version), RefereeLetterOut)
        except LLMError as exc:
            letter.error = str(exc)
            out.append(letter)
            continue
        returned = {i.key: i for i in result.fields if i.key in REFEREE_FIELDS}
        for key in REFEREE_FIELDS:
            item = returned.get(key)
            if item is None or not item.value.strip() or item.value.strip().lower() == NOT_STATED:
                letter.fields[key] = Fact(fact_key=f"referee.{key}", fact_value=NOT_STATED, source=f"document:{doc['id']}")
                continue
            qc = verify_quote(item.quote, red.text, threshold=threshold)
            letter.fields[key] = Fact(
                fact_key=f"referee.{key}", fact_value=item.value.strip(), source_quote=item.quote,
                quote_verified=qc.verified, source=f"document:{doc['id']}",
            )
        for h in result.highlights[:3]:
            qc = verify_quote(h, red.text, threshold=threshold)
            letter.highlights.append({"quote": h, "verified": qc.verified, "method": qc.method, "source": f"document:{doc['id']}"})
        out.append(letter)
    return out
