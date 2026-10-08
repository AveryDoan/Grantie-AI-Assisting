"""PII detection: Presidio + spaCy NER + custom recognisers, then policy.

Policy applied on top of raw detections:
1. Allowlisted phrases (providers, courses, "subclass 500", scholarships,
   NT places) are never redacted - unless the text is one of the applicant's
   own known values (an applicant can be called "Katherine").
2. Countries are kept (rules need country of residence and nationality).
3. Inside referee letters a person becomes REFEREE, not PERSON.
4. Overlapping detections: the longest span wins, then known values, then
   the higher score. Only the sensitive span is replaced - sentences are never
   rewritten.
5. Scores below an entity's min_score but at least flag_score are kept and
   marked low_confidence (default stance: catch more rather than less).

Detections carry positions and types only - never the matched text - so they
are safe to log and count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from redaction.config import RedactionConfig, default_config
from redaction.recognizers import (
    DobRecognizer,
    KnownValue,
    KnownValueRecognizer,
    builtin_recognizers,
    pattern_recognizers,
)

DETECTOR_VERSION = "presidio-2.2.364+spacy-3.8+en_core_web_lg-3.8.0+custom-1"

TextKind = Literal["form", "free_text", "referee_letter", "document"]


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    entity: str          # Presidio entity, e.g. PERSON, KNOWN_VALUE
    token_type: str      # PERSON, REFEREE, EMAIL, PHONE, ADDRESS, PLACE, DOB, ID, URL, HANDLE
    score: float
    low_confidence: bool
    recognizer: str
    source: str | None = None  # for known values: the structured field (never the value)


_ENGINES: dict[str, AnalyzerEngine] = {}


def get_engine(cfg: RedactionConfig) -> AnalyzerEngine:
    """One engine per config (loading the spaCy model is slow)."""
    if cfg.config_hash not in _ENGINES:
        _ENGINES[cfg.config_hash] = build_engine(cfg)
    return _ENGINES[cfg.config_hash]


def build_engine(cfg: RedactionConfig) -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": cfg.detector.get("language", "en"), "model_name": cfg.detector["spacy_model"]}],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON", "PERSON": "PERSON", "NORP": "NRP", "FAC": "LOCATION", "LOC": "LOCATION",
                    "GPE": "LOCATION", "LOCATION": "LOCATION", "ORG": "ORGANIZATION", "DATE": "DATE_TIME",
                },
                "labels_to_ignore": ["ORGANIZATION", "CARDINAL", "EVENT", "LANGUAGE", "LAW", "MONEY", "ORDINAL",
                                     "PERCENT", "PRODUCT", "QUANTITY", "WORK_OF_ART"],
            },
        }
    )
    registry = RecognizerRegistry(supported_languages=["en"])
    registry.add_recognizer(SpacyRecognizer(supported_entities=["PERSON", "LOCATION"]))
    for rec in [*builtin_recognizers(cfg), *pattern_recognizers(cfg),
                DobRecognizer(cfg.dob_context.cues, cfg.dob_context.window)]:
        registry.add_recognizer(rec)
    return AnalyzerEngine(registry=registry, nlp_engine=provider.create_engine(), supported_languages=["en"])


class Detector:
    def __init__(self, cfg: RedactionConfig | None = None) -> None:
        self.cfg = cfg or default_config()
        self.engine = get_engine(self.cfg)
        self.allow = self.cfg.allowlist_patterns()
        self.countries = self.cfg.country_set()
        self.entities = [e for e in self.cfg.entities if e not in ("REFEREE",)]

    def _allowlisted_spans(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for p in self.allow for m in p.finditer(text)]

    def detect(self, text: str, known: list[KnownValue] | None = None, kind: TextKind = "free_text") -> list[Detection]:
        if not text.strip():
            return []
        known = list(known or [])
        known += [KnownValue(d.value, d.type, "denylist") for d in self.cfg.denylist]
        ad_hoc = [KnownValueRecognizer(known)] if known else []
        entities = self.entities if ad_hoc else [e for e in self.entities if e != KnownValueRecognizer.ENTITY]
        raw: list[RecognizerResult] = self.engine.analyze(
            text=text, language="en", entities=entities, score_threshold=self.cfg.flag_score,
            ad_hoc_recognizers=ad_hoc,
        )
        allowed = self._allowlisted_spans(text)
        candidates: list[Detection] = []
        for r in raw:
            meta = r.recognition_metadata or {}
            is_known = r.entity_type == KnownValueRecognizer.ENTITY
            span = text[r.start : r.end]
            if not is_known:
                if any(r.start < e and s < r.end for s, e in allowed):
                    continue  # allowlisted phrase (provider, course, place, scholarship...)
                if r.entity_type == "LOCATION" and span.strip(" .,").casefold() in self.countries:
                    continue  # country of residence / nationality are kept
            token_type = meta.get("token_type") if is_known else self.cfg.token_type(r.entity_type)
            if token_type is None:
                continue
            if token_type == "PERSON" and kind == "referee_letter" and not is_known:
                token_type = "REFEREE"
            start, end = r.start, r.end
            if r.entity_type == "PERSON":
                start, end = _extend_name(text, start, end, allowed)
            low = (not is_known) and r.score < self.cfg.min_score(r.entity_type)
            candidates.append(Detection(start, end, r.entity_type, token_type, round(r.score, 3), low,
                                        meta.get("recognizer_name", "?"), meta.get("source")))
        return _resolve_overlaps(candidates)


_NAME_WORD = re.compile(r"[A-Z][A-Za-z\u00C0-\u024F'\u2019]*(?:-[A-Za-z\u00C0-\u024F]+)*")
_NOT_NAME = frozenset(
    "I The A And Or But In On At To For Of From With By My Our Your We He She They It This That These Those "
    "Mr Mrs Ms Miss Dr Prof Sir Madam Dear Yours Regards".split()
)


def _extend_name(text: str, start: int, end: int, blocked: list[tuple[int, int]]) -> tuple[int, int]:
    """Grow a partial name detection over adjacent capitalised name words.

    Fairness: NER often catches only part of a family-name-first or Pacific
    name ("Nguyen Van" of "Nguyen Van An"). Stops at punctuation, sentence
    starts, common capitalised words and allowlisted phrases.
    """
    def ok(a: int, b: int, word: str) -> bool:
        return word not in _NOT_NAME and not any(a < e and s < b for s, e in blocked)

    while True:  # to the right: " Word" or "-Word"
        m = re.match(r"[ \-]", text[end : end + 1])
        w = _NAME_WORD.match(text, end + 1) if m else None
        if not w or not ok(w.start(), w.end(), w.group()):
            break
        end = w.end()
    while True:  # to the left, unless that word starts a sentence
        if start < 2 or text[start - 1] not in " -":
            break
        m = re.search(r"([A-Z][A-Za-z\u00C0-\u024F'\u2019-]*)$", text[: start - 1])
        if not m or not ok(m.start(1), m.end(1), m.group(1)):
            break
        before = text[: m.start(1)].rstrip()
        if not before or before[-1] in ".!?:\n":
            break  # sentence-initial capital: not evidence of a name
        start = m.start(1)
    return start, end


# On an exact tie of span length, the more specific recogniser wins:
# known values, then structured patterns, then phone numbers, then NER guesses.
_PRIORITY = {
    "KNOWN_VALUE": 0, "EMAIL_ADDRESS": 1, "SOCIAL_URL": 1, "SOCIAL_HANDLE": 1, "DATE_OF_BIRTH": 1,
    "PASSPORT_NUMBER": 2, "VISA_GRANT_NUMBER": 2, "STUDENT_ID": 2, "COE_NUMBER": 2, "AU_ABN": 2, "AU_TFN": 2,
    "AU_ACN": 2, "STREET_ADDRESS": 2, "AU_POSTCODE": 3, "PHONE_NUMBER": 4, "PERSON": 5, "LOCATION": 6,
}


def _resolve_overlaps(cands: list[Detection]) -> list[Detection]:
    """Longest span first, then the more specific recogniser, then score; keep non-overlapping."""
    ordered = sorted(cands, key=lambda d: (-(d.end - d.start), _PRIORITY.get(d.entity, 9), -d.score, d.start))
    kept: list[Detection] = []
    for d in ordered:
        if all(d.end <= k.start or d.start >= k.end for k in kept):
            kept.append(d)
    return sorted(kept, key=lambda d: d.start)
