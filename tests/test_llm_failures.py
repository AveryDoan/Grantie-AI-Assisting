"""Principle 3: failure never looks like success. Plus retry/backoff/cache/fallback."""

import json

import pytest

from app.domain import Rule
from app.llm import LLMClient, LLMError, LLMRateLimited, LLMTimeout
from app.llm.cache import MemoryCache
from app.pipeline.evaluate import evaluate_evidence_only, evaluate_llm_rule
from app.pipeline.prompts import RuleAssessmentOut, rule_prompt
from tests.conftest import ScriptedProvider

RULE = Rule(id="r6", rule_pack_id="p", rule_code="R6", rule_text="Lives in the NT", rule_type="factual", check_method="llm")
JUDGEMENT = Rule(id="r7", rule_pack_id="p", rule_code="R7", rule_text="Community", rule_type="judgement", check_method="llm")
TEXT = "living_arrangements: I live in Darwin."
MET = {"status": "Met", "rationale": "ok", "evidence_quote": "I live in Darwin.", "confidence": "high",
       "language_flag": False, "needs_applicant_clarification": False}


@pytest.mark.parametrize(
    "failure",
    [LLMError("boom"), LLMTimeout("slow"), LLMRateLimited("429"), "not json at all", json.dumps({"status": "Met"}),
     json.dumps(MET | {"status": "Definitely met"})],
    ids=["api-error", "timeout", "rate-limited", "malformed", "missing-fields", "bad-enum"],
)
def test_failure_is_unclear_with_error_flag_never_met(scripted, failure):
    llm, _ = scripted(lambda p, s: failure, max_rate_limit_retries=1)
    f = evaluate_llm_rule(llm, RULE, TEXT, "v1")
    assert f.ai_status == "Unclear"
    assert f.error_flag is True
    assert f.is_valid is False
    assert f.ai_status != "Met"


def test_no_llm_configured_is_unclear(scripted):
    f = evaluate_llm_rule(None, RULE, TEXT, "v1")
    assert f.ai_status == "Unclear" and f.error_flag


def test_invalid_output_retried_once_then_succeeds(scripted):
    answers = iter(["{bad", json.dumps(MET)])
    llm, p = scripted(lambda pr, s: next(answers))
    assert evaluate_llm_rule(llm, RULE, TEXT, "v1").ai_status == "Met"
    assert len(p.calls) == 2


def test_invalid_output_twice_gives_error(scripted):
    llm, p = scripted(lambda pr, s: "{bad")
    f = evaluate_llm_rule(llm, RULE, TEXT, "v1")
    assert f.error_flag and len(p.calls) == 2


def test_rate_limit_exponential_backoff():
    sleeps = []
    answers = iter([LLMRateLimited("429"), LLMRateLimited("429"), json.dumps(MET)])

    def respond(p, s):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    p = ScriptedProvider(respond)
    llm = LLMClient(p, temperature=0.0, sleep=sleeps.append, backoff_base=1.0)
    llm.call_llm(rule_prompt(RULE, TEXT, "v1"), RuleAssessmentOut)
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_fallback_provider_used_when_primary_rate_limited():
    primary = ScriptedProvider(lambda p, s: LLMRateLimited("429"))
    backup = ScriptedProvider(lambda p, s: json.dumps(MET))
    backup.name, backup.model = "groq", "backup-model"
    llm = LLMClient(primary, fallback=backup, temperature=0.0, sleep=lambda _: None, max_rate_limit_retries=0)
    assert llm.call_llm(rule_prompt(RULE, TEXT, "v1"), RuleAssessmentOut).status == "Met"
    assert len(backup.calls) == 1


def test_cache_prevents_second_call():
    p = ScriptedProvider(lambda pr, s: json.dumps(MET))
    llm = LLMClient(p, cache=MemoryCache(), temperature=0.0)
    for _ in range(3):
        llm.call_llm(rule_prompt(RULE, TEXT, "v1"), RuleAssessmentOut)
    assert len(p.calls) == 1 and llm.stats["cache_hits"] == 2


def test_cache_key_changes_with_rule_pack_version():
    p = ScriptedProvider(lambda pr, s: json.dumps(MET))
    llm = LLMClient(p, cache=MemoryCache(), temperature=0.0)
    llm.call_llm(rule_prompt(RULE, TEXT, "v1"), RuleAssessmentOut)
    llm.call_llm(rule_prompt(RULE, TEXT, "v2"), RuleAssessmentOut)
    assert len(p.calls) == 2


def test_temperature_must_be_low():
    with pytest.raises(ValueError):
        LLMClient(ScriptedProvider(lambda p, s: "{}"), temperature=0.7)


def test_judgement_failure_stays_evidence_only(scripted):
    llm, _ = scripted(lambda p, s: LLMError("down"))
    f = evaluate_evidence_only(llm, JUDGEMENT, TEXT, "v1")
    assert f.ai_status == "Evidence only" and f.error_flag and not f.is_valid
