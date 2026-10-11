"""Pipeline step 3: injection screen.

Application text is DATA. This pattern-based detector flags instruction-like
text for the officer. It does not change any finding and nothing is obeyed:
the prompts wrap the text in delimiters and tell the model to ignore
instructions inside it, and `neutralise_delimiters` stops the text from
closing the delimiter early.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|prompt|rule|guideline|previous|above|earlier)s?\b", re.I | re.S)),
    ("role_reassignment", re.compile(r"\b(you are now|act as|pretend to be|from now on you|new instructions?)\b", re.I)),
    ("system_prompt_reference", re.compile(r"\b(system prompt|system message|developer message|hidden instructions?)\b", re.I)),
    ("status_directive", re.compile(r"\b(mark|set|rate|classify|output|return|record)\b.{0,40}\b(as\s+)?[\"']?(met|eligible|approved|pass(ed)?|compliant)[\"']?\b", re.I | re.S)),
    ("approval_demand", re.compile(r"\b(approve|accept|fund) (this|my|our) (application|grant|request)\b", re.I)),
    ("chat_role_marker", re.compile(r"(^|\n)\s*(system|assistant|user)\s*:", re.I)),
    ("delimiter_breakout", re.compile(r"</?\s*(application_data|application|document_data|instructions?|system)\s*>", re.I)),
    ("ai_addressed", re.compile(r"\b(dear|attention|note to|hey)\s+(ai|assistant|model|chatbot|llm|gpt|gemini)\b", re.I)),
    ("tool_or_code", re.compile(r"(```|\{\s*\"status\"\s*:)", re.I)),
]


@dataclass(frozen=True)
class InjectionFlag:
    pattern: str
    source: str  # field name or document id
    excerpt: str  # short excerpt around the match, for the officer

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def screen(text: str, source: str) -> list[InjectionFlag]:
    flags: list[InjectionFlag] = []
    for name, pattern in _RULES:
        for m in pattern.finditer(text):
            start, end = max(0, m.start() - 30), min(len(text), m.end() + 30)
            flags.append(InjectionFlag(name, source, text[start:end].replace("\n", " ").strip()))
            break  # one flag per pattern per source is enough
    return flags


def screen_application(application_text: dict, documents: list[dict] | None = None) -> list[InjectionFlag]:
    flags: list[InjectionFlag] = []
    for section in ("fields", "answers"):
        for key, value in (application_text.get(section) or {}).items():
            if isinstance(value, str):
                flags.extend(screen(value, key))
    for doc in documents or []:
        if doc.get("extracted_text"):
            flags.extend(screen(doc["extracted_text"], f"document:{doc.get('id')}"))
    return flags


def neutralise_delimiters(text: str) -> str:
    """Stop data from closing or opening our delimiter tags."""
    return re.sub(r"<(/?)\s*(application_data|document_data)\s*>", r"‹\1\2›", text, flags=re.I)
