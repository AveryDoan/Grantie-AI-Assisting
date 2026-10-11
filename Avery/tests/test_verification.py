"""Principle 2: every quote is checked by code; fabricated quotes are invalid."""

from app.domain import Finding, Rule
from app.pipeline.verification import verify_findings, verify_quote

SOURCE = "living_arrangements: I live in Darwin near the waterfront.\ncommunity_connection: I coach junior soccer."


def _rule(code: str = "R6", rule_type: str = "factual", method: str = "llm") -> Rule:
    return Rule(id=f"rule-{code}", rule_pack_id="p", rule_code=code, rule_text="x", rule_type=rule_type, check_method=method)


def test_exact_quote_verified():
    assert verify_quote("I live in Darwin near the waterfront.", SOURCE).method == "exact"


def test_whitespace_and_curly_quotes_tolerated():
    qc = verify_quote("I  live in   DARWIN near the waterfront", SOURCE)
    assert qc.verified and qc.method == "normalised"


def test_fabricated_quote_caught():
    qc = verify_quote("I have lived in Darwin for ten years and own a house.", SOURCE)
    assert not qc.verified


def test_fabricated_quote_makes_finding_invalid():
    rule = _rule()
    f = Finding(rule_id=rule.id, rule_code="R6", ai_status="Met", evidence_quote="I was born in Darwin and never left.",
                confidence="high", check_source="llm", is_valid=True)
    [out] = verify_findings([f], [rule], SOURCE)
    assert out.quote_verified is False
    assert out.is_valid is False
    assert "not found" in (out.error_detail or "")


def test_short_quotes_never_fuzzy_matched():
    assert not verify_quote("I live in Perth", SOURCE, min_fuzzy_length=20).verified


def test_fuzzy_threshold_is_configurable():
    near = "I live in Darwin near the waterfrnt"  # one typo
    assert verify_quote(near, SOURCE, threshold=90).verified
    assert not verify_quote(near, SOURCE, threshold=100).verified


def test_every_rule_gets_exactly_one_finding():
    r1, r2 = _rule("R1"), _rule("R2")
    dup = Finding(rule_id=r1.id, rule_code="R1", ai_status="Needs evidence", check_source="llm", is_valid=True)
    out = verify_findings([dup, dup.model_copy()], [r1, r2], SOURCE)
    assert [f.rule_id for f in out] == [r1.id, r2.id]
    assert out[0].is_valid is False  # duplicate produced
    assert out[1].ai_status == "Unclear" and out[1].error_flag  # missing -> error, never Met


def test_llm_met_without_quote_is_invalid():
    rule = _rule()
    f = Finding(rule_id=rule.id, rule_code="R6", ai_status="Met", check_source="llm", is_valid=True, confidence="high")
    [out] = verify_findings([f], [rule], SOURCE)
    assert not out.is_valid
