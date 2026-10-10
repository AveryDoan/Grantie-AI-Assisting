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
        # a future offer is not current study
        "not_if": ["accepted an offer", "offer to start", "offer to study", "will start", "will begin", "plan to", "intend to"],
        "positive": ["secondary school", "high school", "Year 12", "not studying", "working as", "final semester", "final year", "currently studying", "I study at"],
    },
    "SNT-S4": {"evidence": ["IELTS", "English entry", "entry requirement", "academic requirement"]},
    "SNT-M1": {"evidence": ["average", "prize", "examination", "examinations", "exam", "grades", "results", "distinction", "award", "certificate"]},
    "SNT-M3": {"evidence": ["lead", "leader", "led", "coordinator", "organised", "organise"]},
    "SNT-M4": {"evidence": ["volunteer", "community", "clinic"]},
    "SNT-M2": {"evidence": ["volunteer", "community", "clinic", "recommend", "support", "patients", "helps", "helped", "mentor"]},
    "SNT-M5": {"evidence": ["nurse", "Northern Territory", "remote", "community", "clinic", "families", "help"]},
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


def _free_sentences(source: str) -> list[str]:
    """Every sentence in the text, with a form field's "name: " prefix dropped (each is a verbatim part of the text)."""
    out = []
    for line in source.splitlines():
        value = re.sub(r"^[a-z][a-z0-9_]*: ", "", line.strip())
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", value) if s.strip())
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
        if kind == "timeline":
            return json.dumps({"events": _stub_timeline(source)})
        if kind == "narrative":
            return json.dumps({"contradictions": _stub_narrative(source)})
        code = self._qualified(meta.get("rule_code", ""), prompt)
        hints = _HINTS.get(code, {})
        if hints.get("field"):
            sentences = _sentences(source, hints["field"])
        if kind == "evidence":
            quotes = [s for s in sentences if any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I) for k in hints.get("evidence", []))]
            # The offline stub cannot paraphrase: each bullet only names the keyword that made it point at a passage.
            keys = hints.get("evidence", [])
            matched = [(s, next(k for k in keys if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I)))
                       for s in _free_sentences(source) if any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I) for k in keys)]
            summaries = [{"summary": f"Mentions {kw.lower()} in this passage.", "passages": [s]} for s, kw in matched[:6]]
            return json.dumps({"supporting_quotes": quotes[:3], "summaries": summaries})
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
        past = hints.get("not_if", [])
        neg = _find([x for x in sentences if not _find([x], past)], hints.get("negative", []))
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


# ---------------------------------------------------------------------------
# Consistency-layer tasks. Pattern matching over the combined redacted text; NOT an LLM, and written alongside
# the synthetic test cases, so it says nothing about how a real model performs.
# ---------------------------------------------------------------------------

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_WHEN_S = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{4}})"
_RANGE = re.compile(rf"(?<![\d/])(?P<s>{_WHEN_S})\s*(?:-|\u2013|\u2014|to|until)\s*(?P<e>{_WHEN_S}|present|current|now)(?![\d/])", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ROLE_WORDS = re.compile(r"president|manager|engineer|analyst|developer|intern|volunteer|coordinator|captain|lead|director|founder|"
                         r"assistant|officer|teacher|tutor|consultant|designer|member|chair|secretary|treasurer|representative", re.I)
_STUDY_WORDS = re.compile(r"bachelor|master|diploma|degree|studies|studied|student at|studying|secondary school|high school|college|university|certificate", re.I)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _norm_when(text: str) -> str:
    m = re.match(rf"({_MONTH})\.?\s+(\d{{4}})", text.strip(), re.I)
    return f"{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}" if m else text.strip()


def _stub_timeline(source: str) -> list[dict[str, Any]]:
    events = []
    for line in source.splitlines():
        if line.startswith("=== ") or not line.strip():
            continue
        m = _RANGE.search(line)
        if not m:
            continue
        before = re.sub(r"[\s,;:(\-\u2013]+$", "", line[: m.start()]).strip(" -*\u2022\t") or line[m.end():].strip(" ,;:()-")
        kind = "role" if _ROLE_WORDS.search(line) or not _STUDY_WORDS.search(line) else "study"
        if _STUDY_WORDS.search(line) and not _ROLE_WORDS.search(line):
            kind = "study"
        low = line.lower()
        events.append({"label": before[:80] or "Event", "kind": kind, "start": _norm_when(m.group("s")),
                       "end": "present" if m.group("e").lower() in ("present", "current", "now") else _norm_when(m.group("e")),
                       "full_time": True if re.search(r"full[- ]time", low) else False if re.search(r"part[- ]time", low) else None,
                       "quote": line.strip()})
    return events[:40]


def _num(token: str) -> float | None:
    t = token.lower()
    return float(_NUMBER_WORDS[t]) if t in _NUMBER_WORDS else (float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None)


def _stub_narrative(source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    parts = re.split(r"(?m)^=== .* ===\n", source)
    claims, records = [], []
    for part in parts:
        for m in re.finditer(r"[^.\n]*\b(?:studied|study|studying|completed)\b[^.\n]*?\bfor\s+(\w+)\s+years?\b[^.\n]*", part, re.I):
            n = _num(m.group(1))
            if n is not None:
                topic = re.search(r"(?:studied|study|studying|completed)\s+([\w ]{3,40}?)\s+for\b", m.group(0), re.I)
                claims.append((n, m.group(0).strip(), topic.group(1).strip() if topic else "years of study"))
        for m in re.finditer(r"[^\n]*\b(?:years? (?:completed|of study|attended)|duration of (?:study|attendance)|completed\s+\w+\s+years?)\b[^\n]*", part, re.I):
            nums = [n for t in re.findall(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six)\b", m.group(0), re.I) if (n := _num(t)) is not None and n < 15]
            if nums:
                records.append((nums[0], m.group(0).strip()))
    for n, quote, topic in claims:
        for k, rec in records:
            if abs(n - k) >= 1 and quote != rec:
                out.append({"topic": "years of study" if not topic else topic[:40], "first_quote": quote, "second_quote": rec})
                break
    # A referee's "known for N years" against the same letter's "since <Month YYYY>".
    for part in parts:
        known = re.search(r"[^.\n]*\bknown\b[^.\n]*?\bfor\s+(\w+)\s+years?\b[^.\n]*", part, re.I)
        since = re.search(rf"[^.\n]*\b(?:since|joined|started)\b[^.\n]*?({_MONTH}\.?\s+\d{{4}})[^.\n]*", part, re.I)
        dated = re.search(rf"(?im)^\s*date\s*:\s*(?:\d{{1,2}}\s+)?({_MONTH}\.?\s+\d{{4}})", part)
        if known and since and dated and known.group(0) != since.group(0):
            n = _num(known.group(1))
            a, b = _norm_when(since.group(1)).split("-"), _norm_when(dated.group(1)).split("-")
            if n is not None and len(a) == 2 and len(b) == 2:
                months = (int(b[0]) - int(a[0])) * 12 + int(b[1]) - int(a[1])
                if abs(n * 12 - months) > 12:
                    out.append({"topic": "how long the referee has known the applicant", "first_quote": known.group(0).strip(),
                                "second_quote": since.group(0).strip()})
    return out[:6]
