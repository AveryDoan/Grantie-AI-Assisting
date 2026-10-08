"""Rules loader, redaction, documents, parsing and deterministic code checks."""

from datetime import date

import pytest

from app.domain import Fact, Rule
from app.pipeline.code_checks import CodeContext, run_code_check
from app.pipeline.documents import DocumentChecks, check_documents, classify, compare_names
from app.pipeline.facts import FactSet
from app.pipeline.parsing import parse_amount, parse_date
from app.pipeline.redaction import redact
from app.pipeline.rules_loader import RulePackNotApproved, load_rule_pack
from seed import data


# ---------------------------------------------------------------- rules loader
def test_loader_refuses_unapproved_pack(store):
    store.update("rule_packs", {"status": "draft"}, eq={"id": data.SNT_PACK})
    with pytest.raises(RulePackNotApproved):
        load_rule_pack(store, data.SNT_PACK)


def test_loader_refuses_retired_pack(store):
    store.update("rule_packs", {"status": "retired"}, eq={"id": data.SNT_PACK})
    with pytest.raises(RulePackNotApproved):
        load_rule_pack(store, data.SNT_PACK)


def test_loader_returns_version_and_ordered_rules(store):
    pack = load_rule_pack(store, data.SNT_PACK)
    assert pack.version == "v2"
    assert [r.rule_code for r in pack.rules] == [*(f"S{i}" for i in range(1, 17)), *(f"D{i}" for i in range(1, 8)),
                                                *(f"M{i}" for i in range(1, 6))]


def test_run_is_stamped_with_pack_version(store, officer, stub_llm, settings):
    from app.pipeline.orchestrator import run_assessment
    from tests.conftest import app_id

    run = run_assessment(store, officer, app_id("N01"), stub_llm, settings)
    assert run["rule_pack_version"] == "v2" and run["status"] == "complete"


# ---------------------------------------------------------------- redaction
def test_redaction_strips_identifiers_keeps_org_facts():
    text = {
        "fields": {"applicant_name": "Linh Tran", "email": "linh@example.invalid", "region": "Katherine",
                   "requested_amount": "$4,500", "arrival_date": "2026-02-15", "abn": "ABN 12 345 678 901"},
        "answers": {"q": "Linh lives at 12 Smith Street, call 0412 345 678 or email linh@example.invalid. "
                         "Passport N1234567. Contact Ms Jane Citizen. We are a not-for-profit in Katherine."},
    }
    r = redact(text)
    for secret in ("Linh", "Tran", "linh@example.invalid", "12 Smith Street", "0412 345 678", "N1234567", "Jane Citizen"):
        assert secret not in r.text, secret
    for kept in ("Katherine", "$4,500", "2026-02-15", "not-for-profit", "12 345 678 901"):
        assert kept in r.text, kept
    assert "Linh Tran" in r.mapping.values()
    assert r.restore(r.text).count("Linh") >= 1


# ---------------------------------------------------------------- parsing / documents
@pytest.mark.parametrize("s", ["2026-03-12", "12/03/2026", "12-03-2026", "12 March 2026", "12th March 2026", "March 12, 2026", "12 Mar 2026"])
def test_date_formats(s):
    assert parse_date(s) == date(2026, 3, 12)


def test_amounts():
    assert parse_amount("$4,500") == 4500 and parse_amount("4.5k") == 4500 and parse_amount("abc") is None


def test_name_order_and_diacritics_tolerated():
    assert compare_names("Nguyễn Văn Bảo", "BAO VAN NGUYEN", 85)[0] == "match"
    assert compare_names("Mohammed Ali", "Muhammad Ali", 80)[0] == "variant"
    assert compare_names("Maria Garcia", "Kenji Sato", 85)[0] == "mismatch"


PASSPORT = "Passport\nSurname: {family}\nGiven Names: {given}\nNationality: [fictional]\nDate of Birth: {dob}\nDocument Number: {num}\n"


def test_document_classifier():
    assert classify(data.coe("A B")) == "coe"
    assert classify(data.visa("B", "A")) == "visa"
    assert classify(PASSPORT.format(family="B", given="A", dob="2000-01-01", num="N1")) == "travel_document"
    assert classify(data.offer_letter("A B")) == "offer_letter"
    assert classify(data.booking("A B")) == "travel_booking"
    assert classify(data.flight_screenshot()) == "flight_screenshot"  # a screenshot is not a booking
    assert classify(data.referee("A B", "Ms C", "Teacher", "School", "Teacher", "2 years", "1 May 2026")) == "referee_letter"
    assert classify(data.headshot()) == "headshot"
    assert classify("Electricity bill for March") == "other"


def test_typed_date_mismatch_needs_verification_not_fraud():
    docs = [{"id": "d1", "declared_type": "passport", "extracted_text": PASSPORT.format(family="GARCIA", given="MARIA", dob="1998-08-21", num="G1")}]
    checks = check_documents({"applicant_name": "Maria Garcia", "date_of_birth": "1999-08-21"}, docs)
    assert checks.checks[0].needs_verification
    labels = {c["label"] for c in checks.checks[0].comparisons}
    assert "needs verification" in labels and not any("fraud" in str(c).lower() for c in checks.checks[0].comparisons)


def test_ambiguous_numeric_date_is_variant_not_mismatch():
    docs = [{"id": "d1", "declared_type": "passport", "extracted_text": PASSPORT.format(family="LEE", given="JO", dob="2000-04-03", num="Q1")}]
    checks = check_documents({"date_of_birth": "04/03/2000"}, docs)  # typed month-first
    assert checks.checks[0].comparisons[0]["result"] == "variant"


# ---------------------------------------------------------------- code checks
def _code_rule(params: dict, rule_type: str = "factual") -> Rule:
    return Rule(id="r", rule_pack_id="p", rule_code="X1", rule_text="t", rule_type=rule_type, check_method="code", params=params)


def test_unfilled_placeholder_is_unclear_error_not_met():
    rule = _code_rule({"check": "amount_at_most", "fact": "requested_amount", "max": "[maximum grant amount]"})
    facts = FactSet({"requested_amount": Fact(fact_key="requested_amount", fact_value="$100", source_quote="$100", quote_verified=True)})
    f = run_code_check(CodeContext(rule, facts, DocumentChecks([])))
    assert f.ai_status == "Unclear" and f.error_flag and not f.is_valid


def test_missing_fact_is_needs_evidence_not_not_met():
    rule = _code_rule({"check": "amount_at_most", "fact": "requested_amount", "max": "5000"})
    f = run_code_check(CodeContext(rule, FactSet(), DocumentChecks([])))
    assert f.ai_status == "Needs evidence"


def test_failed_fact_extraction_is_unclear():
    rule = _code_rule({"check": "fact_in_list", "fact": "incorporation_act", "allowed": ["Associations Act"]})
    f = run_code_check(CodeContext(rule, FactSet(error="timeout"), DocumentChecks([])))
    assert f.ai_status == "Unclear" and f.error_flag


def test_amount_cap_is_decided_by_code():
    rule = _code_rule({"check": "amount_at_most", "fact": "requested_amount", "max": "5000"})
    over = FactSet({"requested_amount": Fact(fact_key="requested_amount", fact_value="$5,000.01", source_quote="$5,000.01", quote_verified=True)})
    assert run_code_check(CodeContext(rule, over, DocumentChecks([]))).ai_status == "Not met"


def test_max_active_grants_counts_register():
    rule = _code_rule({"check": "max_active_grants", "max_active_grants": 2, "counts_this_application": True, "scope": "program"},
                      "cross_application")
    rows = [{"status": "active", "grant_program_id": "g"}, {"status": "closed", "grant_program_id": "g"},
            {"status": "active", "grant_program_id": "other"}]
    assert run_code_check(CodeContext(rule, FactSet(), DocumentChecks([]), rows, "g")).ai_status == "Met"
    rows.append({"status": "active", "grant_program_id": "g"})
    assert run_code_check(CodeContext(rule, FactSet(), DocumentChecks([]), rows, "g")).ai_status == "Not met"


def test_unverified_fact_is_not_trusted():
    rule = _code_rule({"check": "fact_in_list", "fact": "incorporation_act", "allowed": ["Associations Act"]})
    facts = FactSet({"incorporation_act": Fact(fact_key="incorporation_act", fact_value="Associations Act",
                                              source_quote="made up", quote_verified=False)})
    assert run_code_check(CodeContext(rule, facts, DocumentChecks([]))).ai_status == "Unclear"
