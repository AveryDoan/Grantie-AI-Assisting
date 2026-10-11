"""The redaction pipeline and the fail-closed gate in front of the LLM.

run_redaction():
  1. form fields + answers (structured first, then detection)  - redaction.structured
  2. documents: extract (no OCR), flag manual review, redact     - redaction.extract / .documents
  3. leak scan of everything that would go to the LLM             - redaction.leakscan
  4. status:
     - blocked_redaction_leak : something personal is still there -> NO LLM call
     - needs_manual_review    : documents to read by eye, or low-confidence detections
                                when the config says "block" (then NO LLM call either)
     - ok
Output: redacted texts, token map, location_class, a values-free report,
and the documents needing manual review.

GuardedLLM wraps the LLM client: it refuses any prompt whose application text
did not pass the leak scan, and re-scans the prompt itself before sending.
A refusal raises RedactionBlocked (not an LLM error), so the assessment stops
instead of quietly producing "Unclear" findings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from app.logging_utils import get_logger
from redaction.config import RedactionConfig, default_config
from redaction.detector import DETECTOR_VERSION, Detector, TextKind
from redaction.documents import DocumentRedactor, RedactedDocument, document_source, manual_review_list
from redaction.extract import ExtractedDocument
from redaction.leakscan import LeakScanner, LeakScanResult
from redaction.location import LocationClass
from redaction.recognizers import KnownValue
from redaction.structured import APPLICATION_SOURCE, FieldRedactor
from redaction.tokenizer import TokenMap

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

RunStatus = Literal["ok", "blocked_redaction_leak", "needs_manual_review"]
AiStatus = Literal["ready", "blocked_redaction_leak", "blocked_low_confidence"]


class RedactionBlocked(RuntimeError):
    """The LLM must not be called. The message is safe to log (no values)."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class RedactionOutcome:
    application_id: str
    status: RunStatus
    ai_status: AiStatus
    redacted_text: str
    documents: list[RedactedDocument]
    token_map: TokenMap
    known_values: list[KnownValue] = field(repr=False)
    location_class: LocationClass
    leak_scan: LeakScanResult
    report: dict[str, Any]
    manual_review: list[dict[str, Any]]
    # The first pass, before two-pass consistency (inspection tools only; never stored).
    pass1_token_map: TokenMap | None = field(default=None, repr=False)

    @property
    def llm_allowed(self) -> bool:
        return self.ai_status == "ready"

    def ai_texts(self) -> dict[str, str]:
        """Everything the LLM may see: the application text and included documents."""
        texts = {APPLICATION_SOURCE: self.redacted_text}
        for d in self.documents:
            if d.included_in_ai_input:
                texts[document_source(d.document_id)] = d.redacted_text
        return texts

    def llm_input(self) -> dict[str, str]:
        if not self.llm_allowed:
            raise RedactionBlocked(f"LLM input withheld for application {self.application_id}: {self.ai_status}")
        return self.ai_texts()


def consistency_known_values(token_map: TokenMap, known: list[KnownValue]) -> list[KnownValue]:
    """Every original replaced anywhere, as a known value for the second pass."""
    have = {(k.token_type, k.value.casefold()) for k in known}
    extra: list[KnownValue] = []
    for occs in token_map.occurrences.values():
        for o in occs:
            token_type = token_map.entries[o.token].token_type
            if token_type == "ADDRESS" or len(o.original.strip()) < 3:
                continue  # addresses already come from fields; very short values would over-match
            if token_type in ("PERSON", "REFEREE") and not any(ch.isalpha() for ch in o.original):
                continue  # a name has letters: a year the NER tagged once ("2024") must not vanish everywhere
            key = (token_type, o.original.casefold())
            if key not in have:
                have.add(key)
                extra.append(KnownValue(o.original, token_type, "consistency"))
    return sorted(extra, key=lambda k: (k.token_type, k.value.casefold()))


def run_redaction(
    application_id: str,
    application_text: dict[str, Any],
    documents: list[ExtractedDocument] | None = None,
    *,
    document_kinds: dict[str, TextKind] | None = None,
    cfg: RedactionConfig | None = None,
    detector: Detector | None = None,
    extra_known: list[KnownValue] | None = None,
) -> RedactionOutcome:
    """extra_known: values known from somewhere other than the form (e.g. a parsed
    passport MRZ). They are treated exactly like form-field known values."""
    cfg = cfg or default_config()
    detector = detector or Detector(cfg)
    fields = FieldRedactor(cfg, detector)
    doc_redactor = DocumentRedactor(cfg, detector)

    def one_pass(extra: list[KnownValue]):
        app = fields.redact(application_text, extra_known=extra)
        docs = doc_redactor.redact(documents or [], app.token_map, app.known_values,
                                   kinds=document_kinds, known_addr=fields.known_addr)
        return app, docs

    # Pass 1 finds everything; pass 2 redacts every text again knowing all of it,
    # so a value redacted in one place (an answer) is redacted everywhere (a letterhead).
    extra = list(extra_known or [])
    app, docs = one_pass(extra)
    pass1 = app.token_map
    # Low confidence is judged on what the detectors saw (pass 1); in pass 2 the
    # same values are known values and would no longer look uncertain.
    low = app.low_confidence + sum(d.low_confidence for d in docs)
    app, docs = one_pass(extra + consistency_known_values(app.token_map, app.known_values))

    scanner = LeakScanner(cfg)
    leak = LeakScanResult()
    texts = {APPLICATION_SOURCE: app.redacted_text, **{document_source(d.document_id): d.redacted_text
                                                       for d in docs if d.included_in_ai_input}}
    for source, text in texts.items():
        leak.findings += scanner.scan(text, source, app.known_values, app.token_map).findings

    review = manual_review_list(docs)
    if not leak.ok:
        status, ai_status = "blocked_redaction_leak", "blocked_redaction_leak"
        log.warning("redaction leak scan blocked LLM input: application=%s checks=%s",
                    application_id, sorted(leak.summary()))  # check names and counts only
    elif low and cfg.low_confidence_action == "block":
        status, ai_status = "needs_manual_review", "blocked_low_confidence"
        log.warning("low-confidence detections held LLM input for review: application=%s count=%d", application_id, low)
    else:
        status = "needs_manual_review" if review else "ok"
        ai_status = "ready"

    report = {
        "status": status,
        "ai_status": ai_status,
        "counts": app.token_map.counts(),
        "low_confidence_detections": low,
        "location_class": app.location_class,
        "leak_scan": leak.summary(),
        "leak_sources": sorted({f.source for f in leak.findings}),
        "documents": {"total": len(docs), "in_ai_input": sum(d.included_in_ai_input for d in docs),
                      "needs_manual_review": len(review)},
        "detector_version": DETECTOR_VERSION,
        "config_hash": cfg.config_hash,
    }
    return RedactionOutcome(application_id, status, ai_status, app.redacted_text, docs, app.token_map,
                            app.known_values, app.location_class, leak, report, review, pass1_token_map=pass1)


class GuardedLLM:
    """Wraps an LLM client. Only redacted, leak-scanned text can go through."""

    def __init__(self, inner: Any, outcome: RedactionOutcome, cfg: RedactionConfig | None = None) -> None:
        self.inner = inner
        self.outcome = outcome
        self.scanner = LeakScanner(cfg or default_config())
        self.allowed = {text_hash(t) for t in outcome.ai_texts().values()} if outcome.llm_allowed else set()
        self.blocked_calls = 0

    def combine(self, parts: list[tuple[str, str]]) -> tuple[str, list[tuple[str, int, int, str]]]:
        """One prompt text made of several redacted texts, for checks that compare passages across documents.

        Each part must already be an allowed (redacted, leak-scanned) text, exactly as the redaction produced it.
        The headings are made here from plain labels. The combined text is then allowed as a whole, and the
        prompt is still re-scanned before sending. Returns the text and (label, start, end, part_text) per part.
        """
        if not self.outcome.llm_allowed:
            raise RedactionBlocked(f"LLM call refused for application {self.outcome.application_id}: {self.outcome.ai_status}")
        out, segments, pos = [], [], 0
        for label, text in parts:
            if text_hash(text) not in self.allowed:
                raise RedactionBlocked("combined text refused: a part did not pass redaction")
            if not re.fullmatch(r"[A-Za-z0-9 _:()./-]{1,80}", label):
                raise RedactionBlocked("combined text refused: unsafe heading")
            head = f"=== {label} ===\n"
            start = pos + len(head)
            out.append(head + text + "\n\n")
            segments.append((label, start, start + len(text), text))
            pos += len(out[-1])
        combined = "".join(out)
        self.allowed.add(text_hash(combined))
        return combined, segments

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    def stats(self) -> dict[str, int]:
        return self.inner.stats

    def call_llm(self, prompt: Any, schema: type[T]) -> T:
        app_id = self.outcome.application_id
        if not self.outcome.llm_allowed:
            self.blocked_calls += 1
            raise RedactionBlocked(f"LLM call refused for application {app_id}: {self.outcome.ai_status}")
        if text_hash(prompt.cache_text) not in self.allowed:
            self.blocked_calls += 1
            log.warning("LLM call refused: prompt text was not produced by redaction (application=%s task=%s)",
                        app_id, prompt.task)
            raise RedactionBlocked(f"LLM call refused for application {app_id}: text did not pass redaction")
        rescan = self.scanner.scan(prompt.user, f"prompt:{prompt.task}", self.outcome.known_values, self.outcome.token_map)
        if not rescan.ok:
            self.blocked_calls += 1
            log.warning("LLM call refused: leak scan of the prompt failed (application=%s task=%s checks=%s)",
                        app_id, prompt.task, sorted(rescan.summary()))
            raise RedactionBlocked(f"LLM call refused for application {app_id}: prompt leak scan failed")
        return self.inner.call_llm(prompt, schema)
