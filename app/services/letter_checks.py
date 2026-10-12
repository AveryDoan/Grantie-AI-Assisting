"""Code checks every outcome letter must pass before an officer can sign it off and release it. Code, not the AI.

- Forbidden terms: no rank, score, comparison with other applicants, no fraud/cheating/suspicion words, nothing about the
  AI-use check, consistency signals or redaction.
- Language fairness: a letter never criticises grammar, spelling or style.
- Numbers: every number must come from an allowed source (the grant's settings, the rule text, the applicant's own quote,
  the officer's own reason). No invented counts.
- Placeholders: an unresolved [placeholder] blocks release (the editor highlights it).
- Prompt injection: a quote that contains instruction-like text is flagged (it stays in the letter only as a quote).
- Reading level: computed and shown; above Year 9 is a warning, not a block.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import textstat

from app.pipeline import injection

TARGET_GRADE = 9.0  # about Year 8-9 (Flesch-Kincaid grade)

# Whole words or phrases, matched case-insensitively. Each is a reason the letter cannot be released as written.
FORBIDDEN = [
    r"rank(?:s|ed|ing)?", r"scor(?:e|es|ed|ing)", r"percentile", r"lower than", r"higher than", r"better than", r"worse than",
    r"other applicants?", r"other applications?", r"weaker", r"stronger", r"competitiveness", r"most competitive",
    r"fraud(?:ulent)?", r"cheat(?:s|ed|ing)?", r"suspicious", r"fake", r"ai[- ]generated", r"detector", r"redact(?:ed|ion|ions)?",
    r"ai[- ]use check", r"consistency (?:check|signal|flag)s?", r"how many (?:people|applicants) applied", r"the ai (?:has )?decided",
]
_FORBIDDEN = re.compile(r"\b(?:" + "|".join(FORBIDDEN) + r")\b", re.I)
# A letter never criticises how something was written.
_STYLE = re.compile(r"\b(?:grammar|spelling|punctuation|poorly written|writing style|your english|poor english|language skills)\b", re.I)
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{2,80}\]")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w])")
QUOTE_LINE = re.compile(r'^What you wrote: "(.*)"\s*$', re.M)
_REASON_HEAD = re.compile(r"^Reason \d+: rule \S+", re.M)


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text or "")}


def flatten(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from flatten(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from flatten(v)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def strip_quotes(body: str) -> str:
    """The officer's and the template's words only: the applicant's quoted words are theirs, not ours."""
    return QUOTE_LINE.sub("", body)


def check(body: str, *, allowed_sources: Iterable[str], quotes: Iterable[str] = ()) -> dict[str, Any]:
    prose = strip_quotes(body)
    forbidden = sorted({m.group(0).lower() for m in _FORBIDDEN.finditer(prose)})
    style = sorted({m.group(0).lower() for m in _STYLE.finditer(prose)})
    placeholders = sorted(set(PLACEHOLDER.findall(prose)))
    allowed = set().union(*(numbers_in(s) for s in allowed_sources)) if allowed_sources else set()
    unknown_numbers = sorted(numbers_in(_REASON_HEAD.sub("", prose)) - allowed, key=lambda n: (len(n), n))
    flagged_quotes = [q for q in quotes if q and injection.screen(q, "letter")]
    grade = round(float(textstat.flesch_kincaid_grade(prose or ".")), 1)
    return {
        "forbidden_terms": forbidden,
        "no_forbidden_terms": not forbidden,
        "style_criticism": style,
        "no_style_criticism": not style,
        "placeholders": placeholders,
        "no_placeholders": not placeholders,
        "unknown_numbers": unknown_numbers,
        "numbers_from_allowed_sources": not unknown_numbers,
        "quotes_with_instructions": flagged_quotes,          # a warning: shown as a quote only, never followed
        "reading_grade": grade,
        "reading_grade_target": TARGET_GRADE,
        "reading_grade_ok": grade <= TARGET_GRADE,           # a warning only
        # kept for older screens and tests: the letter has no score or ranking language
        "no_score_or_ranking_language": not forbidden,
    }


BLOCKING = ("no_forbidden_terms", "no_style_criticism", "no_placeholders", "numbers_from_allowed_sources")
