"""Pipeline step 5: fact extraction.

Facts typed into structured form fields are taken directly by code (the
field value is its own quote). Only facts that live in free text are asked
of the LLM, which must quote exactly and answer "not stated" rather than
guess. Every quote is then verified by code; unverified facts are not
trusted by the rule checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import NOT_STATED, Fact, Rule
from app.llm import LLMClient, LLMError
from app.pipeline.prompts import FactExtractionOut, facts_prompt
from app.pipeline.verification import verify_quote


@dataclass
class FactSet:
    facts: dict[str, Fact] = field(default_factory=dict)
    error: str | None = None  # set when the LLM extraction failed

    def get(self, key: str) -> Fact | None:
        return self.facts.get(key)


def required_fact_keys(rules: list[Rule]) -> set[str]:
    keys: set[str] = set()
    for r in rules:
        if "fact" in r.params:
            keys.add(r.params["fact"])
        keys.update(r.params.get("facts", []))
    return keys


def extract_facts(
    llm: LLMClient | None,
    rules: list[Rule],
    typed_fields: dict[str, Any],
    redacted_text: str,
    rule_pack_version: str,
    *,
    threshold: float = 92.0,
) -> FactSet:
    wanted = required_fact_keys(rules)
    result = FactSet()

    for key in sorted(wanted):
        value = typed_fields.get(key)
        if value not in (None, ""):
            result.facts[key] = Fact(
                fact_key=key, fact_value=str(value), source_quote=str(value), quote_verified=True, source=f"field:{key}"
            )

    free_text_keys = sorted(wanted - result.facts.keys())
    if not free_text_keys:
        return result
    if llm is None:
        result.error = "No LLM configured for fact extraction"
        return result

    try:
        out = llm.call_llm(facts_prompt(free_text_keys, redacted_text, rule_pack_version), FactExtractionOut)
    except LLMError as exc:
        result.error = str(exc)
        return result

    returned = {f.key: f for f in out.facts if f.key in free_text_keys}
    for key in free_text_keys:
        item = returned.get(key)
        if item is None or item.value.strip().lower() == NOT_STATED or not item.value.strip():
            result.facts[key] = Fact(fact_key=key, fact_value=NOT_STATED, source="llm")
            continue
        qc = verify_quote(item.quote, redacted_text, threshold=threshold)
        result.facts[key] = Fact(
            fact_key=key,
            fact_value=item.value.strip(),
            source_quote=item.quote,
            quote_verified=qc.verified,
            source="llm",
        )
    return result
