"""Step 4: leak scan - the last check before any text can reach an LLM.

Re-scans REDACTED text for:
- email addresses,
- phone-number shapes (the configured international/Australian patterns),
- long digit strings (8+ digits, allowing single spaces or hyphens),
- every known value from the structured fields (in all the forms the
  known-value recogniser matches: case, accents, name order, date formats),
- every original value already replaced anywhere in this application.

Anything found -> not safe. Findings name the CHECK and the SOURCE only,
never the value, so they are safe to log, store and show.
Text inside our own tokens ("[PERSON_1]", "[LOCATION: NT, Australia]") is
ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from redaction.config import RedactionConfig, default_config
from redaction.recognizers import KnownValue, known_value_patterns
from redaction.tokenizer import TokenMap

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# 8+ digits, optionally in space-separated groups ("1234 5678 9012"). Hyphens,
# slashes and dots are NOT joined, so dates (2026-10-05, 20/09/2026) never match;
# hyphenated numbers are covered by the phone patterns.
LONG_DIGITS = re.compile(r"(?<![\d\-/.])\d(?: ?\d){7,}(?![\d\-/.])")
TOKEN = re.compile(r"\[(?:[A-Z]+_\d+|LOCATION:[^\]]*)\]")


@dataclass(frozen=True)
class LeakFinding:
    check: str        # email_pattern | phone_pattern | long_digit_string | known_value | redacted_original
    source: str       # where it was found, e.g. "application_text" or "document:<id>"
    detail: str = ""  # field name or token TYPE - never the value


@dataclass
class LeakScanResult:
    findings: list[LeakFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.check] = out.get(f.check, 0) + 1
        return out


def _mask_tokens(text: str) -> str:
    """Blank out our own tokens (same length) so they never match a check."""
    return TOKEN.sub(lambda m: " " * len(m.group()), text)


class LeakScanner:
    def __init__(self, cfg: RedactionConfig | None = None) -> None:
        self.cfg = cfg or default_config()
        phone = self.cfg.patterns.get("INTERNATIONAL_PHONE")
        self.phone_res = [re.compile(s.pattern) for s in phone.regex] if phone else []

    def scan(self, text: str, source: str, known: list[KnownValue] | None = None,
             tokens: TokenMap | None = None) -> LeakScanResult:
        result = LeakScanResult()
        t = _mask_tokens(text)
        if EMAIL.search(t):
            result.findings.append(LeakFinding("email_pattern", source))
        if any(p.search(t) for p in self.phone_res):
            result.findings.append(LeakFinding("phone_pattern", source))
        if LONG_DIGITS.search(t):
            result.findings.append(LeakFinding("long_digit_string", source))
        for kv in known or []:
            for pattern, _ in known_value_patterns(kv):
                if re.search(pattern, t, re.IGNORECASE):
                    result.findings.append(LeakFinding("known_value", source, kv.source))
                    break
        if tokens is not None:
            # The exact text replaced at every occurrence (includes full addresses,
            # which the token map itself only shows as a location placeholder).
            seen: set[str] = set()
            for occs in tokens.occurrences.values():
                for o in occs:
                    original = o.original.strip()
                    key = original.casefold()
                    if len(original) < 3 or key in seen:
                        continue
                    seen.add(key)
                    if re.search(rf"(?<![\w]){re.escape(original)}(?![\w])", t, re.IGNORECASE):
                        result.findings.append(LeakFinding("redacted_original", source,
                                                           tokens.entries[o.token].token_type))
        return result
