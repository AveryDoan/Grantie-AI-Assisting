"""Step 4: leak scan and fail-closed behaviour (synthetic data only)."""

from __future__ import annotations

import copy
import json
import logging

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from app.domain import Rule  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.pipeline.prompts import RuleAssessmentOut, rule_prompt  # noqa: E402
from redaction.config import load_config  # noqa: E402
from redaction.detector import Detector  # noqa: E402
from redaction.extract import extract_document  # noqa: E402
from redaction.leakscan import LeakScanner  # noqa: E402
from redaction.pipeline import GuardedLLM, RedactionBlocked, run_redaction  # noqa: E402
from redaction.recognizers import KnownValue  # noqa: E402
from seed import data  # noqa: E402
from tests.conftest import ScriptedProvider  # noqa: E402
from tests.redaction.pdfs import make_pdf  # noqa: E402

N01 = next(c for c in data.CASES if c["code"] == "N01")["application_text"]
PLANTED_EMAIL = "planted.leak@example.invalid"
PLANTED_PHONE = "+61 412 999 888"
RULE = Rule(id="r", rule_pack_id="p", rule_code="S8", rule_text="Not studying with an NT provider.",
            rule_type="factual", check_method="llm")
MET = {"status": "Met", "rationale": "ok", "evidence_quote": None, "confidence": "high",
       "language_flag": False, "needs_applicant_clarification": False}


@pytest.fixture(scope="module")
def detector() -> Detector:
    return Detector()


class BlindDetector(Detector):
    """Simulates a redactor miss: sees nothing in one field (so planted values survive)."""

    def __init__(self, base: Detector, blind_marker: str) -> None:
        self.__dict__.update(base.__dict__)
        self.marker = blind_marker

    def detect(self, text, known=None, kind="free_text"):
        return [] if self.marker in text else super().detect(text, known, kind)


def scripted_llm():
    provider = ScriptedProvider(lambda p, s: json.dumps(MET))
    return LLMClient(provider, temperature=0.0), provider


# ---------------------------------------------------------------- clean run


def test_clean_application_passes_and_llm_may_be_called(detector):
    out = run_redaction("app-1", N01, detector=detector)
    assert out.status == "ok" and out.ai_status == "ready" and out.leak_scan.ok
    llm, provider = scripted_llm()
    guarded = GuardedLLM(llm, out)
    guarded.call_llm(rule_prompt(RULE, out.redacted_text, "v2"), RuleAssessmentOut)
    assert len(provider.calls) == 1


# ---------------------------------------------------------------- 6. planted leaks block the LLM


@pytest.mark.parametrize("planted, check", [(PLANTED_EMAIL, "email_pattern"), (PLANTED_PHONE, "phone_pattern")])
def test_planted_email_or_phone_blocks_the_llm(detector, planted, check):
    app = copy.deepcopy(N01)
    app["answers"]["community_engagement"] = f"MARKER contact the clinic at {planted} for details."
    out = run_redaction("app-2", app, detector=BlindDetector(detector, "MARKER"))
    assert planted in out.redacted_text                 # the redactor missed it...
    assert out.status == "blocked_redaction_leak"       # ...and the leak scan caught it
    assert out.ai_status == "blocked_redaction_leak" and not out.llm_allowed
    assert check in out.report["leak_scan"]
    with pytest.raises(RedactionBlocked):
        out.llm_input()
    llm, provider = scripted_llm()
    with pytest.raises(RedactionBlocked):
        GuardedLLM(llm, out).call_llm(rule_prompt(RULE, out.redacted_text, "v2"), RuleAssessmentOut)
    assert provider.calls == []                         # fail closed: the provider was never reached


def test_known_value_leak_is_caught_even_without_a_pattern(detector):
    """A name has no pattern shape: the known-value re-scan catches it."""
    app = copy.deepcopy(N01)
    app["answers"]["leadership"] = "MARKER Linh Tran organised four workshops."
    out = run_redaction("app-3", app, detector=BlindDetector(detector, "MARKER"))
    assert out.status == "blocked_redaction_leak" and "known_value" in out.report["leak_scan"]


def test_leak_in_a_document_blocks_too(detector):
    letter = ["To whom it may concern,", "MARKER Please call me on +61 412 999 888.", "Date: 12 May 2026"]
    doc = extract_document("doc-1", "ref.pdf", make_pdf([letter]))
    out = run_redaction("app-4", N01, [doc], detector=BlindDetector(detector, "MARKER"))
    assert out.status == "blocked_redaction_leak" and out.report["leak_sources"] == ["document:doc-1"]


# ---------------------------------------------------------------- the gate itself


def test_gate_refuses_text_that_did_not_come_from_redaction(detector):
    out = run_redaction("app-5", N01, detector=detector)
    llm, provider = scripted_llm()
    unredacted = "applicant_name: Linh Tran\nemail: linh.tran@example.invalid"
    with pytest.raises(RedactionBlocked, match="did not pass redaction"):
        GuardedLLM(llm, out).call_llm(rule_prompt(RULE, unredacted, "v2"), RuleAssessmentOut)
    assert provider.calls == []


def test_gate_rescans_the_whole_prompt(detector):
    """Even with approved application text, a leak added elsewhere in the prompt is refused."""
    out = run_redaction("app-6", N01, detector=detector)
    prompt = rule_prompt(RULE, out.redacted_text, "v2")
    tampered = type(prompt)(**{**prompt.__dict__, "user": prompt.user + "\nContact: linh.tran@example.invalid"})
    llm, provider = scripted_llm()
    with pytest.raises(RedactionBlocked, match="prompt leak scan"):
        GuardedLLM(llm, out).call_llm(tampered, RuleAssessmentOut)
    assert provider.calls == []


def test_low_confidence_block_mode_holds_the_llm(detector, tmp_path):
    from redaction.config import DEFAULT_PATH

    p = tmp_path / "block.yaml"
    p.write_text(DEFAULT_PATH.read_text().replace("low_confidence_action: redact", "low_confidence_action: block"))
    cfg = load_config(p)
    app = copy.deepcopy(N01)
    app["answers"]["current_study"] += " My reference is 987654."  # an ID-shaped number with no context: low confidence
    out = run_redaction("app-7", app, cfg=cfg, detector=Detector(cfg))
    assert out.report["low_confidence_detections"] >= 1
    assert out.status == "needs_manual_review" and out.ai_status == "blocked_low_confidence" and not out.llm_allowed


# ---------------------------------------------------------------- scanner precision


def test_scanner_ignores_tokens_dates_and_rule_numbers():
    scanner = LeakScanner()
    text = ("applicant_name: [PERSON_1]\nresidential_address: [LOCATION: outside Australia]\n"
            "course_start_date: 2026-10-05\narrival: 20/09/2026\nI hold a subclass 500 visa. Fees $45,000. 4,000 characters.")
    assert scanner.scan(text, "t").ok


def test_scanner_finds_long_digit_strings():
    assert not LeakScanner().scan("Reference 1234 5678 9012", "t").ok


# ---------------------------------------------------------------- 11. logs contain no values


def test_blocked_run_logs_no_personal_values(detector, caplog):
    app = copy.deepcopy(N01)
    app["answers"]["community_engagement"] = f"MARKER email {PLANTED_EMAIL} or Linh Tran."
    with caplog.at_level(logging.DEBUG):
        out = run_redaction("app-8", app, detector=BlindDetector(detector, "MARKER"))
        llm, _ = scripted_llm()
        with pytest.raises(RedactionBlocked) as exc:
            GuardedLLM(llm, out).call_llm(rule_prompt(RULE, out.redacted_text, "v2"), RuleAssessmentOut)
    everything = caplog.text + str(exc.value) + repr(out.report) + repr(out.leak_scan.findings) + repr(out.manual_review)
    for value in (PLANTED_EMAIL, "Linh", "Tran", "linh.tran", "+84 900"):
        assert value not in everything, value
    assert "blocked" in caplog.text  # a safe warning was logged


def test_leak_findings_name_the_field_not_the_value():
    kv = KnownValue("Linh Tran", "PERSON", "applicant_name", "applicant")
    r = LeakScanner().scan("Linh Tran was here", "application_text", [kv])
    assert [(f.check, f.source, f.detail) for f in r.findings] == [("known_value", "application_text", "applicant_name")]
