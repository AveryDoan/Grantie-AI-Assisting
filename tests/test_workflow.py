"""Officer review, sign-off, audit log, clarification, letters and pre-check."""

import pytest

from app.pipeline.orchestrator import run_assessment
from app.services import letters, merit, precheck, review
from app.services.errors import Conflict, NotFound, SignOffBlocked, ValidationFailed
from app.store.base import StoreError
from seed import data
from tests.conftest import app_id


def assess(store, officer, stub_llm, settings, code="N04"):
    run = run_assessment(store, officer, app_id(code), stub_llm, settings)
    findings = {store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"]: f
                for f in store.select("findings", eq={"run_id": run["id"]})}
    return run, findings


def decide_all(store, officer, findings, *, skip=()):
    for code, f in findings.items():
        if code in skip:
            continue
        app = store.select("applications", eq={"id": f["application_id"]})[0]
        if code in merit.merit_codes(store, app):  # merit criteria are marked by the officer, not confirmed
            merit.set_mark(store, officer, app["id"], code, mark=70, not_assessed=False, reason="Officer read the evidence")
            continue
        if f["ai_status"] == "Evidence only" or not f["is_valid"]:
            review.review_finding(store, officer, f["id"], action="override", final_status="Met", reason="Officer read the evidence")
        elif f["ai_status"] == "Not met":
            review.review_finding(store, officer, f["id"], action="confirm", reason="Officer checked the document dates")
        else:
            review.review_finding(store, officer, f["id"], action="confirm")


# ---------------------------------------------------------------- reviews
def test_override_without_reason_rejected(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    for reason in (None, "", "   "):
        with pytest.raises(ValidationFailed, match="reason"):
            review.review_finding(store, officer, f["D1"]["id"], action="override", final_status="Needs evidence", reason=reason)


def test_not_met_always_needs_reason_even_on_confirm(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    assert f["S2"]["ai_status"] == "Not met"
    with pytest.raises(ValidationFailed, match="Not met"):
        review.review_finding(store, officer, f["S2"]["id"], action="confirm")


def test_confirm_cannot_change_status(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    with pytest.raises(ValidationFailed, match="override"):
        review.review_finding(store, officer, f["D1"]["id"], action="confirm", final_status="Not met", reason="x")


def test_evidence_only_cannot_be_confirmed(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    with pytest.raises(ValidationFailed, match="Evidence only"):
        review.review_finding(store, officer, f["M1"]["id"], action="confirm", final_status="Met")


def test_review_writes_audit_with_ai_suggestion_and_decision(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    review.review_finding(store, officer, f["D1"]["id"], action="override", final_status="Needs evidence", reason="CoE is unreadable")
    row = store.select("audit_log", eq={"action": "finding.review"})[-1]
    assert row["ai_suggestion"] == "Met" and row["officer_decision"] == "Needs evidence"
    assert row["overridden"] is True and row["reason"] == "CoE is unreadable" and row["rule_pack_version"] == "v2"


def test_other_organisation_cannot_review(store, officer, other_officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    with pytest.raises(NotFound):
        review.review_finding(store, other_officer, f["D1"]["id"], action="confirm")


# ---------------------------------------------------------------- sign-off
def test_signoff_blocked_until_all_findings_reviewed(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    with pytest.raises(SignOffBlocked) as e:
        review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)
    assert len(e.value.details["pending_rules"]) == 28

    decide_all(store, officer, f, skip={"S8"})
    with pytest.raises(SignOffBlocked) as e:
        review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)
    assert e.value.details["pending_rules"] == ["S8"]

    review.review_finding(store, officer, f["S8"]["id"], action="ask_applicant")
    with pytest.raises(SignOffBlocked):  # asking the applicant is not a decision
        review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)

    review.review_finding(store, officer, f["S8"]["id"], action="confirm")
    with pytest.raises(ValidationFailed):
        review.sign_off(store, officer, app_id("N04"), statement_acknowledged=False)
    review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)
    assert store.select("applications", eq={"id": app_id("N04")})[0]["status"] == "signed_off"
    assert store.select("audit_log", eq={"action": "application.signoff"})


def test_no_reviews_or_second_signoff_after_signoff(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    decide_all(store, officer, f)
    review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)
    with pytest.raises(Conflict):
        review.review_finding(store, officer, f["D1"]["id"], action="confirm")
    with pytest.raises(Conflict):
        review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)


def test_signoff_without_any_run_blocked(store, officer):
    with pytest.raises(SignOffBlocked):
        review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)


def test_no_bulk_approve_function_exists():
    import app.services.review as r

    assert not [n for n in dir(r) if "bulk" in n.lower() or "approve_all" in n.lower()]


# ---------------------------------------------------------------- audit log
def test_audit_log_cannot_be_updated_or_deleted(store, officer, stub_llm, settings):
    assess(store, officer, stub_llm, settings)
    row = store.select("audit_log")[0]
    with pytest.raises(StoreError, match="append-only"):
        store.update("audit_log", {"reason": "tampered"}, eq={"id": row["id"]})
    with pytest.raises(StoreError, match="append-only"):
        store.delete("audit_log", eq={"id": row["id"]})


def test_every_pipeline_action_is_audited(store, officer, stub_llm, settings):
    assess(store, officer, stub_llm, settings)
    actions = {r["action"] for r in store.select("audit_log")}
    assert {"assessment.started", "assessment.completed"} <= actions


def test_assess_is_idempotent(store, officer, stub_llm, settings):
    first = run_assessment(store, officer, app_id("N01"), stub_llm, settings)
    again = run_assessment(store, officer, app_id("N01"), stub_llm, settings)
    assert again["id"] == first["id"] and again["reused"] is True
    forced = run_assessment(store, officer, app_id("N01"), stub_llm, settings, force=True)
    assert forced["id"] != first["id"]


# ---------------------------------------------------------------- clarification
def test_clarification_mock_send(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    c = review.create_clarification(store, officer, app_id("N04"), finding_id=f["S2"]["id"], send=True)
    assert c["status"] == "sent" and "spelling and grammar do not matter" in c["message_text"]
    assert store.select("applications", eq={"id": app_id("N04")})[0]["status"] == "awaiting_applicant"
    assert store.select("audit_log", eq={"action": "clarification.sent_mock"})


# ---------------------------------------------------------------- letters
def test_letter_requires_all_decisions_and_uses_only_confirmed(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    with pytest.raises(Conflict, match="decision"):
        letters.generate_letter(store, officer, app_id("N04"))
    decide_all(store, officer, f)
    letter = letters.generate_letter(store, officer, app_id("N04"))
    # Only the officer-confirmed unmet rules: course start (S2) and referee letter date (D5).
    assert set(letter["source_finding_ids"]) == {f["S2"]["id"], f["D5"]["id"]}
    body = letter["body_text"]
    for part in ("What the rule requires:", "What you wrote:", "Why this did not meet the rule: Officer checked the document dates",
                 "What would change the outcome:", "How to ask for a review:"):
        assert part in body
    qc = letter["quality_checks"]
    assert qc["quotes_match_application"] and qc["every_reason_cites_confirmed_rule"]
    assert qc["no_score_or_ranking_language"] and qc["includes_review_info"]
    assert isinstance(qc["reading_grade"], float)


def test_letter_quotes_are_checked_against_application(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings, code="N07")
    decide_all(store, officer, f)
    letter = letters.generate_letter(store, officer, app_id("N07"))
    quote = "I am currently studying a Diploma of Health at Charles Darwin University in Darwin."
    assert f'What you wrote: "{quote}"' in letter["body_text"]
    tampered = letter["body_text"].replace("I am currently studying a Diploma", "I refuse to study a Diploma")
    edited = letters.update_letter(store, officer, letter["id"], body_text=tampered)
    assert edited["quality_checks"]["quotes_match_application"] is False
    assert edited["status"] == "edited"
    with pytest.raises(Conflict, match="required check"):
        letters.update_letter(store, officer, letter["id"], approve=True)


def test_letter_approval_requires_signoff_then_freezes(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    decide_all(store, officer, f)
    letter = letters.generate_letter(store, officer, app_id("N04"))
    with pytest.raises(Conflict, match="Sign off"):
        letters.update_letter(store, officer, letter["id"], approve=True)
    review.sign_off(store, officer, app_id("N04"), statement_acknowledged=True)
    approved = letters.update_letter(store, officer, letter["id"], approve=True)
    assert approved["status"] == "approved" and approved["approved_by"] == officer.user_id
    with pytest.raises(Conflict):
        letters.update_letter(store, officer, letter["id"], body_text="changed")


def test_applicant_sees_letter_only_after_approval(store, officer, applicant_actor, stub_llm, settings):
    from app.services.queue import application_detail

    _, f = assess(store, officer, stub_llm, settings, code="N01")
    review.review_finding(store, officer, f["S8"]["id"], action="override", final_status="Not met", reason="Already enrolled with an NT provider (phone call)")
    decide_all(store, officer, {k: v for k, v in f.items() if k != "S8"})
    letter = letters.generate_letter(store, officer, app_id("N01"))
    view = application_detail(store, applicant_actor, app_id("N01"))
    assert view["letters"] == [] and "findings" not in view
    review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    letters.update_letter(store, officer, letter["id"], approve=True)
    assert len(application_detail(store, applicant_actor, app_id("N01"))["letters"]) == 1


# ---------------------------------------------------------------- pre-check
def test_precheck_reports_missing_and_wrong_type_and_always_offers_options(store, applicant_actor):
    out = precheck.precheck(
        store, applicant_actor, grant_program_id=data.SNT_PROGRAM,
        fields={"applicant_name": "Test Person"},
        documents=[
            {"declared_type": "travel booking", "file_name": "my_flight.txt", "text": data.flight_screenshot()},
            {"declared_type": "referee letter", "file_name": "ref1.txt",
             "text": data.referee("Test Person", "Ms A", "Teacher", "Fictional School", "Teacher", "2 years", "1 May 2026")},
        ],
    )
    assert {m["field"] for m in out["missing_fields"]} >= {"arrival_date", "residential_country", "current_study", "biography"}
    missing = {m["document_type"]: m for m in out["missing_documents"]}
    assert set(missing) == {"coe", "travel_booking", "referee_letter", "headshot"}
    assert missing["referee_letter"]["needed"] == 2 and missing["referee_letter"]["provided"] == 1
    assert out["wrong_document_type"][0]["file_name"] == "my_flight.txt"
    assert out["options"]["submit_anyway"]["available"] and out["options"]["ask_a_person"]["available"]
    assert "eligible" not in {k.lower() for k in out}


def test_precheck_clean_still_offers_options(store, applicant_actor):
    case = next(c for c in data.CASES if c["code"] == "N01")
    fields = case["application_text"]["fields"] | case["application_text"]["answers"]
    docs = [{"declared_type": d, "text": t} for d, t in case["documents"]]
    out = precheck.precheck(store, applicant_actor, grant_program_id=data.SNT_PROGRAM, fields=fields, documents=docs)
    assert out["items_to_check"] == 0 and out["options"]["submit_anyway"]["available"]


def test_repeating_the_same_decision_is_a_no_op(store, officer, stub_llm, settings):
    _, f = assess(store, officer, stub_llm, settings)
    first = review.review_finding(store, officer, f["S2"]["id"], action="confirm", reason="Course starts in 2027")
    again = review.review_finding(store, officer, f["S2"]["id"], action="confirm", reason="Course starts in 2027")
    assert again["id"] == first["id"]
    assert len(store.select("officer_reviews", eq={"finding_id": f["S2"]["id"]})) == 1
    assert len([a for a in store.select("audit_log", eq={"action": "finding.review"})]) == 1
