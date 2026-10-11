"""Custom Presidio recognisers for Study NT applications.

All detection is local: regular expressions, Presidio's phonenumbers-based
phone recogniser, and spaCy NER (wired in redaction.detector). No cloud
service and no LLM is involved.

KnownValueRecognizer is the safety net: names, emails, phones, addresses,
DOB and ID numbers taken from the structured form fields are searched for in
all free text and documents, so the applicant's own details are caught even
when the statistical detector misses them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

import regex as regex_mod  # Presidio's regex engine; same flag values as `re`
from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts
from presidio_analyzer.predefined_recognizers import (
    AuAbnRecognizer,
    AuAcnRecognizer,
    AuTfnRecognizer,
    EmailRecognizer,
    PhoneRecognizer,
)

from redaction.config import RedactionConfig

# Patterns mean exactly what the config says: no implicit IGNORECASE.
CASE_SENSITIVE = regex_mod.M | regex_mod.S

# Name particles that are only redacted as part of a full name, never alone
# (they are also ordinary words or very common across many names).
# Titles are not part of a name: "Ms" from "Ms Rosa Example" must not become a token on its own.
HONORIFICS = frozenset("mr mrs ms miss mx dr prof sir madam".split())
NAME_PARTICLES = frozenset(
    "de da do dos das di du del della la le van von der den bin binti bte ibn al el abu ap mac st".split()
)


# ---------------------------------------------------------------------------
# Pattern recognisers from config
# ---------------------------------------------------------------------------


def pattern_recognizers(cfg: RedactionConfig) -> list[EntityRecognizer]:
    out: list[EntityRecognizer] = []
    for entity, group in cfg.patterns.items():
        target = "PHONE_NUMBER" if entity == "INTERNATIONAL_PHONE" else entity
        patterns = [Pattern(name=f"{entity.lower()}_{i}", regex=spec.pattern, score=spec.score or group.score)
                    for i, spec in enumerate(group.regex)]
        out.append(
            PatternRecognizer(
                supported_entity=target,
                name=f"{entity.title().replace('_', '')}Recognizer",
                patterns=patterns,
                context=group.context or None,
                global_regex_flags=CASE_SENSITIVE,
            )
        )
    return out


def builtin_recognizers(cfg: RedactionConfig) -> list[EntityRecognizer]:
    """Presidio's own recognisers we rely on (all local)."""
    return [
        EmailRecognizer(),
        PhoneRecognizer(supported_regions=tuple(cfg.phone_regions)),
        AuAbnRecognizer(),
        AuTfnRecognizer(),
        AuAcnRecognizer(),
    ]


# ---------------------------------------------------------------------------
# Date of birth: a date close after a "born / DOB / date of birth" cue
# ---------------------------------------------------------------------------

_MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(
    rf"\b(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?,?\s+\d{{4}}|{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}})\b",
    re.IGNORECASE,
)


class DobRecognizer(EntityRecognizer):
    """Dates are kept (rules need them) EXCEPT a date of birth, found by context."""

    def __init__(self, cues: list[str], window: int = 40) -> None:
        self.cue_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(c) for c in cues) + r")(?!\w)", re.IGNORECASE)
        self.window = window
        super().__init__(supported_entities=["DATE_OF_BIRTH"], name="DobContextRecognizer")

    def load(self) -> None:  # nothing to load
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        results = []
        for m in DATE_RE.finditer(text):
            before = text[max(0, m.start() - self.window) : m.start()]
            # Same line only, so "Date of birth:" in one field does not mark the next line's date.
            before = before.split("\n")[-1]
            if self.cue_re.search(before):
                results.append(RecognizerResult("DATE_OF_BIRTH", m.start(), m.end(), 0.85,
                                                recognition_metadata={"recognizer_name": self.name}))
        return results


# ---------------------------------------------------------------------------
# Structural name cues: titles and labelled lines
# ---------------------------------------------------------------------------

_NAME_WORDS = r"[A-Z][A-Za-z\u00C0-\u024F'\u2019\-]+(?:\s+[A-Z][A-Za-z\u00C0-\u024F'\u2019\-]+){0,3}"


class NameCueRecognizer(EntityRecognizer):
    """A person named after a title ("Mr Minh Le") or on a labelled line ("Referee name: ...").

    These are structural signals the NER model can miss, especially for short
    names and names from outside its training data. Only the name is matched;
    the title or label is kept.
    """

    def __init__(self, honorifics: list[str], labels: list[str]) -> None:
        self.honorific_re = re.compile(
            r"\b(?:" + "|".join(re.escape(h) for h in honorifics) + r")\.?\s+(" + _NAME_WORDS + r")"
        ) if honorifics else None
        self.label_re = re.compile(
            r"(?im)^[ \t]*(?:" + "|".join(re.escape(l) for l in sorted(labels, key=len, reverse=True))
            + r")[ \t]*:[ \t]*(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?[ \t]+)?([^\n\[\]]*?[A-Za-z][^\n\[\]]*?)[ \t]*$"
        ) if labels else None
        super().__init__(supported_entities=["PERSON"], name="NameCueRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        out = []
        for regex, score in ((self.honorific_re, 0.75), (self.label_re, 0.7)):
            if regex is None:
                continue
            for m in regex.finditer(text):
                value = m.group(1)
                # A labelled value must look like a name: 1-5 capitalised words, no digits.
                if regex is self.label_re and not re.fullmatch(r"(?:[A-Z][\w'\u2019.\-]*\s*){1,5}", value.strip()):
                    continue
                out.append(RecognizerResult("PERSON", m.start(1), m.start(1) + len(value.rstrip()), score,
                                            recognition_metadata={"recognizer_name": self.name}))
        return out


# ---------------------------------------------------------------------------
# Known values from structured fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownValue:
    value: str
    token_type: str  # PERSON, REFEREE, EMAIL, PHONE, ADDRESS, DOB, ID
    source: str      # field name (never the value)
    group: str | None = None  # same person across fields, e.g. "applicant"


def _fold_char(c: str) -> str:
    base = "".join(ch for ch in unicodedata.normalize("NFKD", c) if not unicodedata.combining(ch))
    base = {"đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ß": "ss"}.get(c, base)
    if base and base != c:
        return f"(?:{re.escape(c)}|{re.escape(base)})"
    return re.escape(c)


def accent_insensitive(value: str) -> str:
    """Regex matching `value` with or without diacritics, flexible whitespace and hyphens."""
    parts = re.split(r"[\s\-]+", value.strip())
    return r"[\s\-]+".join("".join(_fold_char(c) for c in part) for part in parts if part)


def parse_any_date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y",
                "%d %B, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value.strip()), fmt).date()
        except ValueError:
            continue
    return None


def _date_variants(value: str) -> list[str]:
    parsed: date | None = None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).date()
            break
        except ValueError:
            continue
    if not parsed:
        return [value]
    d, m, y = parsed.day, parsed.month, parsed.year
    month, mon = parsed.strftime("%B"), parsed.strftime("%b")
    return list(dict.fromkeys([
        value, parsed.isoformat(), f"{y}/{m:02d}/{d:02d}", f"{d:02d}/{m:02d}/{y}", f"{d}/{m}/{y}", f"{d:02d}-{m:02d}-{y}", f"{d:02d}.{m:02d}.{y}",
        f"{d} {month} {y}", f"{d:02d} {month} {y}", f"{d} {mon} {y}", f"{month} {d}, {y}", f"{m:02d}/{d:02d}/{y}",
    ]))


def _phone_pattern(value: str) -> list[str]:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 6:
        return []
    variants = {digits}
    if value.strip().startswith("+"):
        for cc_len in (1, 2, 3):  # +84 900... -> 0900...
            variants.add("0" + digits[cc_len:])
    sep = r"[\s().\-]*"
    return [r"(?<!\d)\+?" + sep.join(v) + r"(?!\d)" for v in sorted(variants, key=len, reverse=True)]


def known_value_patterns(kv: KnownValue) -> list[tuple[str, str]]:
    """(regex, token_type) pairs for one known value. Longest first."""
    v = kv.value.strip()
    if not v:
        return []
    if kv.token_type in ("PERSON", "REFEREE"):
        def name_case(word: str) -> str:
            """The word as written, Capitalised or UPPER - never in lower case."""
            forms = dict.fromkeys([word, word[:1].upper() + word[1:].lower(), word.upper()])
            return "(?-i:" + "|".join(accent_insensitive(f) for f in forms) + ")"

        # The full name in its original order: any case (people type their own name in lower case).
        out = [(rf"(?<![\w\[]){accent_insensitive(v)}(?!\w)", kv.token_type)]
        words = [w for w in re.split(r"[\s,]+", v) if w and w.casefold().strip(".") not in HONORIFICS]
        if len(words) >= 2:
            # Other orders, as on ID documents ("TRAN, Linh", "An Nguyen Van"): only where
            # written as a name, so "for example, Ruth" or "we may hope" are left alone.
            for order in (words[-1:] + words[:-1], words[1:] + words[:1]):
                if order != words:
                    out.append((rf"(?<![\w\[])" + r",?\s+".join(name_case(w) for w in order) + r"(?!\w)", kv.token_type))
        # Each meaningful name part on its own - but only where it is written as a
        # name (Capitalised or UPPER). A part that is also a word ("Hope", "May",
        # "Example") must not turn "for example" into a token and change the meaning.
        for part in dict.fromkeys(words):
            if len(part) >= 2 and part.casefold().strip(".") not in NAME_PARTICLES:
                out.append((rf"(?<![\w\[]){name_case(part)}(?!\w)", kv.token_type))
        return out
    if kv.token_type == "PHONE":
        return [(p, "PHONE") for p in _phone_pattern(v)]
    if kv.token_type == "DOB":
        return [(rf"(?<!\w){re.escape(x)}(?!\w)", "DOB") for x in _date_variants(v)]
    if kv.token_type == "ID":
        compact = re.sub(r"[\s\-]", "", v)
        body = r"[\s\-]?".join(re.escape(c) for c in compact)  # tolerate spaces/hyphens inside the ID
        return [(r"(?<![\w])" + body + r"(?![\w])", "ID")]
    if kv.token_type == "ADDRESS":
        pats = [r"[\s,]+".join(re.escape(p) for p in re.split(r"[\s,]+", v) if p)]
        first = v.split(",")[0].strip()
        if first and first != v and len(first) >= 6:
            pats.append(re.escape(first))
        return [(p, "ADDRESS") for p in pats]
    return [(rf"(?<![\w.]){re.escape(v)}(?![\w])", kv.token_type)]


class KnownValueRecognizer(EntityRecognizer):
    """Finds the applicant's (and referees') known values anywhere. Score 1.0."""

    ENTITY = "KNOWN_VALUE"

    def __init__(self, known: list[KnownValue]) -> None:
        self.known = known
        self.compiled: list[tuple[re.Pattern[str], str, str, str | None]] = []
        for kv in known:
            for pat, token_type in known_value_patterns(kv):
                self.compiled.append((re.compile(pat, re.IGNORECASE), token_type, kv.source, kv.group))
        super().__init__(supported_entities=[self.ENTITY], name="KnownValueRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None) -> list[RecognizerResult]:
        out = []
        for pattern, token_type, source, group in self.compiled:
            for m in pattern.finditer(text):
                out.append(RecognizerResult(self.ENTITY, m.start(), m.end(), 1.0,
                                            recognition_metadata={"recognizer_name": self.name, "token_type": token_type,
                                                                  "source": source, "group": group}))
        return out
