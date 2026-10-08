"""OfflineStubProvider - a deterministic keyword matcher. NOT an LLM.

Used for tests, offline demos and the sample evaluation report when no
GEMINI_API_KEY is available. It reads `prompt.meta` (never sent to a real
provider) and answers with the same schemas a real model would, quoting
sentences verbatim from the redacted text. Its accuracy says nothing about
a real model's accuracy; reports produced with it are labelled as such.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMInvalidOutput, Prompt

# Keyword hints for the seeded rule codes only.
_HINTS: dict[str, dict[str, list[str]]] = {
    "SNT-R6": {
        "negative": ["live in Sydney", "living in Sydney", "live in Melbourne", "studying online from", "outside the NT"],
        "positive": ["Darwin", "Alice Springs", "Katherine", "Palmerston", "Northern Territory", "in the NT"],
    },
    "CBF-C1": {
        "negative": ["Pty Ltd", "privately owned company", "profits to shareholders", "shareholders"],
        "positive": ["not-for-profit", "not for profit", "non-profit", "nonprofit", "no profit", "charity"],
    },
    "CBF-C2": {
        "negative": [],
        "positive": ["Darwin", "Alice Springs", "Katherine", "Palmerston", "Tennant Creek", "Northern Territory", "in the NT"],
    },
    "SNT-R7": {"evidence": ["community", "volunteer", "contribute", "help other"]},
    # Study NT v2
    "SNT-S8": {
        "field": ["current_study"],
        "negative": ["Charles Darwin University", "studying with an NT provider", "enrolled at CDU", "study at CDU"],
        "positive": ["secondary school", "high school", "Year 12", "not studying", "working as"],
    },
    "SNT-S4": {"evidence": ["IELTS", "English entry", "entry requirement", "academic requirement"]},
    "SNT-M1": {"evidence": ["average", "prize", "examination", "exam", "grades", "results"]},
    "SNT-M3": {"evidence": ["lead", "leader", "led", "coordinator", "organised", "organise"]},
    "SNT-M4": {"evidence": ["volunteer", "community", "clinic"]},
    "CBF-C6": {"evidence": ["community", "residents", "benefit", "local people", "elders"]},
}
_AMBIGUOUS = ["might", "maybe", "not sure", "planning to", "hope to", "or possibly"]
_ACT = re.compile(
    r"(?:incorporated|registered|incorporation)\s+(?:is\s+)?(?:under|with|by)\s+(?:the\s+)?((?:[A-Z][\w-]*\s+){1,4}Act(?:\s+\d{4})?(?:\s*\([A-Za-z]+\))?)"
)


def _sentences(source: str, fields: list[str] | None = None) -> list[str]:
    out = []
    for line in source.splitlines():
        key, _, value = line.partition(": ")
        if fields and key not in fields:
            continue
        for s in re.split(r"(?<=[.!?])\s+", value):
            if s.strip():
                out.append(s.strip())
    return out


def _find(sentences: list[str], keywords: list[str]) -> str | None:
    for s in sentences:
        for kw in keywords:
            if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", s, re.IGNORECASE):
                return s
    return None


class OfflineStubProvider:
    name = "offline-stub"
    model = "keyword-stub-v1"

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        meta = prompt.meta
        source: str = meta.get("source", "")
        sentences = _sentences(source)
        kind = meta.get("kind")
        if kind == "facts":
            return json.dumps({"facts": [self._fact(k, sentences) for k in meta.get("keys", [])]})
        if kind == "referee":
            return json.dumps(self._referee(source))
        code = self._qualified(meta.get("rule_code", ""), prompt)
        hints = _HINTS.get(code, {})
        if hints.get("field"):
            sentences = _sentences(source, hints["field"])
        if kind == "evidence":
            quotes = [s for s in sentences if any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I) for k in hints.get("evidence", []))]
            return json.dumps({"supporting_quotes": quotes[:3]})
        if kind == "rule":
            return json.dumps(self._rule(hints, sentences))
        raise LLMInvalidOutput("stub: unknown prompt kind")

    @staticmethod
    def _qualified(rule_code: str, prompt: Prompt) -> str:
        prefix = "CBF" if rule_code.startswith("C") else "SNT"
        return f"{prefix}-{rule_code}"

    _REFEREE_LABELS = {
        "referee name": "referee_name", "position": "position", "organisation": "organisation",
        "relationship": "relationship", "known applicant for": "length_of_association",
        "length of association": "length_of_association", "date": "letter_date", "signature": "signature",
    }

    @classmethod
    def _referee(cls, source: str) -> dict[str, Any]:
        found: dict[str, dict[str, Any]] = {}
        for line in source.splitlines():
            label, sep, value = line.partition(":")
            key = cls._REFEREE_LABELS.get(label.strip().lower())
            if sep and key and value.strip() and key not in found:
                found[key] = {"key": key, "value": value.strip(), "quote": line.strip()}
            if "letterhead" in line.lower() and "letterhead" not in found:
                found["letterhead"] = {"key": "letterhead", "value": "yes", "quote": line.strip()}
        keys = ["referee_name", "position", "organisation", "relationship", "length_of_association", "letter_date",
                "signature", "letterhead"]
        fields = [found.get(k, {"key": k, "value": "not stated", "quote": None}) for k in keys]
        highlights = [s for s in re.split(r"(?<=[.!?])\s+", source.replace("\n", " "))
                      if re.search(r"dedicated|hard-working|excellent|led |organised", s)][:3]
        return {"fields": fields, "highlights": [h.strip() for h in highlights]}

    @staticmethod
    def _fact(key: str, sentences: list[str]) -> dict[str, Any]:
        if key == "incorporation_act":
            for s in sentences:
                m = _ACT.search(s)
                if m:
                    return {"key": key, "value": m.group(1).strip(), "quote": s}
        return {"key": key, "value": "not stated", "quote": None}

    @staticmethod
    def _rule(hints: dict[str, list[str]], sentences: list[str]) -> dict[str, Any]:
        neg = _find(sentences, hints.get("negative", []))
        if neg:
            return {"status": "Not met", "rationale": "The applicant's words contradict the rule.", "evidence_quote": neg,
                    "confidence": "medium", "language_flag": False, "needs_applicant_clarification": False}
        pos = _find(sentences, hints.get("positive", []))
        if pos:
            ambiguous = any(a in pos.lower() for a in _AMBIGUOUS)
            return {
                "status": "Unclear" if ambiguous else "Met",
                "rationale": "The wording could be read more than one way." if ambiguous else "The applicant's words address the rule.",
                "evidence_quote": pos,
                "confidence": "low" if ambiguous else "medium",
                "language_flag": False,
                "needs_applicant_clarification": ambiguous,
            }
        return {"status": "Needs evidence", "rationale": "The application does not address this rule.", "evidence_quote": None,
                "confidence": "medium", "language_flag": False, "needs_applicant_clarification": True}
