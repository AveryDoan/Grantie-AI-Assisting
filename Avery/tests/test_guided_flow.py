"""The guided flow: step gating, document decisions and requests, the redaction check (reveal log, leak scan), assessment,
decline-letter drafting and sign-off. Every case is synthetic."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from app.services import doc_step, letters, merit, outcome, redaction_check, review, steps
from app.services.errors import Conflict, NotFound, SignOffBlocked, ValidationFailed
from tests.conftest import app_id
from tests.test_api import token

REQUIRED = ["coe", "arrival", "letter1", "letter2", "bio"]


@pytest.fixture
def guided(settings):
    return settings.model_copy(update={"require_redaction_approval": True})


def app_of(store, code="N01"):
    return store.select("applications", eq={"id": app_id(code)})[0]


def status_of(store, code="N01"):
    return {s["step"]: s["status"] for s in steps.state(store, app_of(store, code))["steps"]}


def confirm_documents(store, officer, code="N01", settings=None):
    for slot in REQUIRED:
        doc_step.decide(store, officer, app_id(code), slot, "confirmed", None, settings)


def to_step_2(store, officer, guided, code="N01"):
    confirm_documents(store, officer, code, guided)
    return steps.complete_documents(store, officer, app_of(store, code), guided)


def to_step_3(store, officer, guided, stub_llm, code="N01"):
    to_step_2(store, officer, guided, code)
    return redaction_check.approve(store, officer, app_id(code), guided, stub_llm)


def decide_assessment(store, officer, code="N01", skip=()):
    run = steps.latest_run(store, app_id(code))
    for f in store.select("findings", eq={"run_id": run["id"]}):
        rule = store.select("rules", eq={"id": f["rule_id"]})[0]
        if rule["rule_code"] in skip:
            continue
        if (rule.get("params") or {}).get("section") == "merit":
            merit.set_mark(store, officer, app_id(code), rule["rule_code"], mark=60, not_assessed=False, reason="Read it")
        elif f["ai_status"] == "Evidence only" or not f["is_valid"]:
            review.review_finding(store, officer, f["id"], action="override", final_status="Met", reason="Officer judgement")
        elif f["ai_status"] == "Not met":
            review.review_finding(store, officer, f["id"], action="confirm", reason="Checked the document")
        else:
            review.review_finding(store, officer, f["id"], action="confirm")


# ------------------------------------------------------------------ gating


def test_a_new_application_starts_at_step_1_and_nothing_later_is_unlocked(store, officer, guided):
    s = steps.state(store, app_of(store), guided)
    assert [x["status"] for x in s["steps"]] == ["not_started"] * 4
    assert [x["unlocked"] for x in s["steps"]] == [True, False, False, False]
    assert s["current"] == 1 and s["legacy"] is False


def test_the_ai_is_blocked_until_step_2_is_approved(store, officer, guided, stub_llm):
    with pytest.raises(Conflict, match="redaction check"):
        steps.assert_ai_allowed(store, officer, app_of(store), guided)
    assert store.select("assessment_runs", eq={"application_id": app_id("N01")}) == []
    to_step_2(store, officer, guided)
    with pytest.raises(Conflict):
        steps.assert_ai_allowed(store, officer, app_of(store), guided)       # redacted, but not approved yet
    redaction_check.approve(store, officer, app_id("N01"), guided, stub_llm)
    steps.assert_ai_allowed(store, officer, app_of(store), guided)
    assert store.select("assessment_runs", eq={"application_id": app_id("N01")})


def test_the_api_refuses_to_assess_before_approval(store, guided, stub_llm, officer):
    limiter.reset()
    client = TestClient(create_app(store=store, settings=guided, llm_factory=lambda s, c: stub_llm))
    h = token(guided, officer.user_id)
    r = client.post(f"/applications/{app_id('N01')}/assess", headers=h, json={})
    assert r.status_code == 409 and "approve" in r.json()["message"].lower()
    assert [a for a in store.select("audit_log") if a["action"] == "assessment.refused"]


def test_a_step_cannot_be_skipped(store, officer, guided, stub_llm):
    with pytest.raises(Conflict, match="Finish step 1"):
        redaction_check.approve(store, officer, app_id("N01"), guided, stub_llm)
    with pytest.raises(Conflict, match="not finished"):
        steps.complete_documents(store, officer, app_of(store), guided)       # nothing confirmed yet
    to_step_2(store, officer, guided)
    s = status_of(store)
    assert s == {1: "done", 2: "in_progress", 3: "not_started", 4: "not_started"}
    with pytest.raises(Conflict, match="Finish step 2"):
        steps.require_unlocked(store, app_of(store), 3, guided)


def test_legacy_applications_keep_working(store, officer, stub_llm, guided):
    from app.pipeline.orchestrator import run_assessment

    run_assessment(store, officer, app_id("N04"), stub_llm, guided)          # assessed before the flow existed
    s = steps.state(store, app_of(store, "N04"), guided)
    assert s["legacy"] and [x["status"] for x in s["steps"][:2]] == ["done", "done"]
    steps.assert_ai_allowed(store, officer, app_of(store, "N04"), guided)


# ------------------------------------------------------------------ step 1: documents


def test_step_1_needs_every_required_document_confirmed_or_a_reason(store, officer, guided):
    view = doc_step.view(store, officer, app_id("N01"))
    assert [s["slot"] for s in view["slots"] if s["required"]] == REQUIRED and len(view["missing"]) == 5
    for slot in REQUIRED[:-1]:
        doc_step.decide(store, officer, app_id("N01"), slot, "confirmed", None, guided)
    with pytest.raises(Conflict):
        steps.complete_documents(store, officer, app_of(store), guided)
    with pytest.raises(ValidationFailed, match="why"):
        doc_step.decide(store, officer, app_id("N01"), "bio", "not_needed", "  ", guided)
    doc_step.decide(store, officer, app_id("N01"), "bio", "not_needed", "The applicant has no photo to give", guided)
    assert steps.complete_documents(store, officer, app_of(store), guided)["steps"][0]["status"] == "done"


def test_marking_a_wrong_slot_or_requesting_again_does_not_finish_the_step(store, officer, guided):
    confirm_documents(store, officer, settings=guided)
    doc_step.decide(store, officer, app_id("N01"), "arrival", "wrong_slot", None, guided)
    assert doc_step.missing_for_step1(store, app_of(store))
    with pytest.raises(Conflict):
        steps.complete_documents(store, officer, app_of(store), guided)


def test_a_document_that_does_not_match_the_form_gets_a_plain_reason_before_any_ai_step(store, officer):
    view = doc_step.view(store, officer, app_id("N04"))
    assert all("reason" in s for s in view["slots"])
    assert any(d.get("attention_level") for d in store.select("documents", eq={"application_id": app_id("N04")}))


# ------------------------------------------------------------------ the request to the applicant


def draft(store, officer, slots=("coe",)):
    return doc_step.draft_request(store, officer, app_id("N01"), [{"slot": s, "kind": "missing"} for s in slots])


def test_a_request_is_a_draft_until_the_officer_approves_and_sends_it(store, officer, guided):
    row = draft(store, officer, ("coe", "arrival"))
    assert row["status"] == "draft" and row["kind"] == "documents" and row["sent_at"] is None
    assert app_of(store)["status"] == "submitted"                       # drafting changes nothing for the applicant
    assert "Confirmation of Enrolment" in row["message_text"] and "Why we need it" in row["message_text"]
    assert "Evidence of arrival date" in row["message_text"]
    edited = doc_step.edit_request(store, officer, row["id"], row["message_text"] + "\nThank you.")
    assert edited["status"] == "draft" and edited["message_text"].endswith("Thank you.")
    assert [a["action"] for a in store.select("audit_log") if a["action"].startswith("request.")] == ["request.drafted", "request.edited"]
    sent = doc_step.approve_and_send(store, officer, row["id"], guided)
    assert sent["status"] == "sent" and sent["sent_at"] and sent["approved_by"] == officer.user_id
    assert app_of(store)["status"] == "awaiting_applicant"
    assert status_of(store)[1] == "waiting"
    with pytest.raises(Conflict):
        doc_step.approve_and_send(store, officer, row["id"], guided)
    with pytest.raises(Conflict):
        doc_step.edit_request(store, officer, row["id"], "changed after sending")
    assert [a for a in store.select("audit_log") if a["action"] == "request.approved_sent"]


def test_a_request_is_in_plain_english_and_names_each_item_and_why(store, officer):
    import textstat

    row = draft(store, officer, ("coe", "letter1", "bio"))
    for label in ("Confirmation of Enrolment", "Letter of Support #1", "Biography and headshot"):
        assert label in row["message_text"]
    assert row["message_text"].count("Why we need it:") == 3
    assert textstat.flesch_kincaid_grade(row["message_text"]) <= 9
    for word in ("fraud", "fake", "suspicious"):
        assert word not in row["message_text"].lower()


def test_a_reply_replaces_the_files_keeps_the_old_ones_and_reopens_step_1(store, officer, applicant_actor, guided):
    import base64

    confirm_documents(store, officer, settings=guided)
    row = draft(store, officer, ("coe",))
    doc_step.approve_and_send(store, officer, row["id"], guided)
    old = doc_step.assign(store.select("documents", eq={"application_id": app_id("N01")}))["coe"]
    text = "Confirmation of Enrolment\nStudent Name: Linh Tran\nCourse: Bachelor of Nursing\nCourse Start Date: 5 October 2026\nSAMPLE: FICTIONAL TEST DOCUMENT"
    out = doc_step.respond(store, applicant_actor, app_id("N01"), [{"file_name": "new_coe.txt", "declared_type": "coe",
                                                                   "content_base64": base64.b64encode(text.encode()).decode()}], guided)
    assert out["resubmitted_at"]
    docs = store.select("documents", eq={"application_id": app_id("N01")})
    new = doc_step.assign(docs)["coe"]
    assert new["id"] != old["id"] and new["replaces_id"] == old["id"]
    assert next(d for d in docs if d["id"] == old["id"])["superseded"] is True
    view = doc_step.view(store, officer, app_id("N01"))
    coe = next(s for s in view["slots"] if s["slot"] == "coe")
    assert coe["replaces"]["old"]["file_name"] != coe["replaces"]["new"]["file_name"]       # old and new, side by side
    assert coe["decision"] is None                                                          # the new file needs a fresh decision
    assert view["requests"][0]["resubmitted_at"]
    assert app_of(store)["status"] in ("submitted", "in_review")
    assert status_of(store)[1] == "in_progress"
    assert [a for a in store.select("audit_log") if a["action"] == "request.resubmitted"]


def test_a_stranger_cannot_reply_and_a_reply_needs_an_open_request(store, officer, applicant_actor):
    with pytest.raises(Conflict, match="no open request"):
        doc_step.respond(store, applicant_actor, app_id("N01"), [{"file_name": "x.txt", "declared_type": "coe", "content_base64": "eA=="}])
    with pytest.raises(NotFound):
        doc_step.respond(store, applicant_actor, app_id("N02"), [{"file_name": "x.txt", "declared_type": "coe", "content_base64": "eA=="}])


# ------------------------------------------------------------------ editing an earlier step reopens the later ones


def test_editing_a_finished_step_reopens_the_later_steps_with_a_notice(store, officer, guided, stub_llm):
    to_step_3(store, officer, guided, stub_llm)
    assert status_of(store)[2] == "done" and status_of(store)[3] == "in_progress"
    doc_step.decide(store, officer, app_id("N01"), "bio", "not_needed", "Changed my mind", guided)       # edit step 1
    s = steps.state(store, app_of(store), guided)["steps"]
    assert [x["status"] for x in s] == ["in_progress", "not_started", "not_started", "not_started"]
    assert s[1]["notice"] == "Later steps need to be checked again."
    assert s[2]["unlocked"] is False
    with pytest.raises(Conflict):
        steps.assert_ai_allowed(store, officer, app_of(store), guided)
    rows = [a["details"] for a in store.select("audit_log") if a["action"] == "step.changed"]
    assert {"step": 1, "key": "documents", "from": "done", "to": "in_progress", "notice": False} in rows
    assert any(r["step"] == 2 and r["to"] == "not_started" and r["notice"] for r in rows)


def test_every_step_change_is_audited_with_officer_time_step_from_and_to(store, officer, guided, stub_llm):
    to_step_3(store, officer, guided, stub_llm)
    changes = [a for a in store.select("audit_log") if a["action"] == "step.changed"]
    assert [(c["details"]["step"], c["details"]["from"], c["details"]["to"]) for c in changes] == [
        (1, "not_started", "in_progress"), (1, "in_progress", "done"), (2, "not_started", "in_progress"), (2, "in_progress", "done")]
    assert all(c["actor_id"] == officer.user_id and c["occurred_at"] and c["application_id"] == app_id("N01") for c in changes)


# ------------------------------------------------------------------ step 2: redaction check


def test_the_redaction_table_groups_by_type_and_hides_original_values(store, officer, guided):
    to_step_2(store, officer, guided)
    ov = redaction_check.overview(store, officer, app_id("N01"), guided)
    kinds = {g["type"]: g for g in ov["groups"]}
    assert "Name" in kinds and "Email" in kinds and kinds["Name"]["count"] >= 2
    assert all(t.startswith("[") for g in ov["groups"] for t in g["how"] if not t.startswith("+"))       # tokens, not values
    blob = json.dumps(ov)
    for private in ("Linh", "Tran", "linh.tran", "@example.com"):
        assert private not in blob
    items = redaction_check.group_items(store, officer, app_id("N01"), "Name", guided)
    assert items and all("original" not in i and "_original" not in i for i in items)
    assert not any(v in json.dumps(items) for v in ("Linh Tran", "linh.tran"))
    assert all(i["token"] in (i["before"] + i["token"] + i["after"]) for i in items)


def test_revealing_a_value_is_audit_logged_without_the_value(store, officer, guided):
    to_step_2(store, officer, guided)
    item = redaction_check.group_items(store, officer, app_id("N01"), "Name", guided)[0]
    assert not [a for a in store.select("audit_log") if a["action"] == "redaction.reveal"]
    out = redaction_check.reveal(store, officer, app_id("N01"), item["id"], guided)
    assert out["original"] and "[" not in out["original"]
    rows = [a for a in store.select("audit_log") if a["action"] == "redaction.reveal"]
    assert len(rows) == 1 and rows[0]["actor_id"] == officer.user_id and rows[0]["occurred_at"]
    assert rows[0]["details"] == {"item_id": item["id"], "type": "Name", "token": item["token"]}
    assert out["original"] not in json.dumps(rows[0])
    redaction_check.reveal(store, officer, app_id("N01"), item["id"], guided)
    assert len([a for a in store.select("audit_log") if a["action"] == "redaction.reveal"]) == 2          # every reveal, not just the first


def test_a_failed_leak_scan_blocks_approval(store, officer, guided, stub_llm):
    to_step_2(store, officer, guided)
    run = store.select("redaction_runs", eq={"application_id": app_id("N01")})[-1]
    store.update("redaction_runs", {"leak_scan": {"email": 1}, "status": "blocked_redaction_leak"}, eq={"id": run["id"]})
    ov = redaction_check.overview(store, officer, app_id("N01"), guided)
    assert ov["leak_scan"]["passed"] is False and ov["blockers"]
    with pytest.raises(Conflict, match="cannot be approved"):
        redaction_check.approve(store, officer, app_id("N01"), guided, stub_llm)
    assert store.select("assessment_runs", eq={"application_id": app_id("N01")}) == []
    assert status_of(store)[2] == "in_progress"


def test_a_clean_leak_scan_is_reported_as_passed(store, officer, guided):
    to_step_2(store, officer, guided)
    assert redaction_check.overview(store, officer, app_id("N01"), guided)["leak_scan"]["passed"] is True


def test_adding_a_missed_item_redacts_it_stores_it_encrypted_and_reopens_the_step(store, officer, guided, stub_llm):
    to_step_3(store, officer, guided, stub_llm)
    texts = redaction_check.overview(store, officer, app_id("N01"), guided)["texts"]["application_text"]["redacted"]
    import re

    outside_tokens = re.sub(r"\[[^\]]*\]", " ", texts.split("current_study:")[1])
    word = next(w for w in re.findall(r"[a-z]{7,}", outside_tokens) if texts.count(w) == 1)
    ov = redaction_check.add_missed(store, officer, app_id("N01"), "application_text", word, "Name", guided)
    assert ov["edits"]["added"] == 1
    assert word not in app_of(store)["redacted_text"]
    rows = store.select("redaction_edits", eq={"application_id": app_id("N01")})
    assert rows and word not in json.dumps(rows) and rows[0]["encrypted_value"].startswith("v1:")
    audit = [a for a in store.select("audit_log") if a["action"] == "redaction.added"]
    assert audit and word not in json.dumps(audit[0]["details"])
    assert status_of(store)[2] == "in_progress" and status_of(store)[3] == "not_started"        # approved step reopened


def test_unmasking_needs_a_reason_and_a_known_personal_value_stays_redacted(store, officer, guided):
    to_step_2(store, officer, guided)
    name = next(i for i in redaction_check.group_items(store, officer, app_id("N01"), "Name", guided))
    with pytest.raises(ValidationFailed, match="why"):
        redaction_check.unmask(store, officer, app_id("N01"), name["id"], "  ", guided)
    with pytest.raises(ValidationFailed, match="known personal"):
        redaction_check.unmask(store, officer, app_id("N01"), name["id"], "I think it is fine", guided)
    assert not store.select("redaction_edits", eq={"application_id": app_id("N01"), "kind": "unmask"})


# ------------------------------------------------------------------ step 3 and 4: assessment, decline letter, sign-off


def to_decline(store, officer, guided, stub_llm, code="N04"):
    confirm_documents(store, officer, code, guided)
    steps.complete_documents(store, officer, app_of(store, code), guided)
    redaction_check.approve(store, officer, app_id(code), guided, stub_llm)


def test_confirming_not_met_drafts_the_decline_letter_at_once_and_it_is_not_released(store, officer, applicant_actor, guided, stub_llm):
    to_decline(store, officer, guided, stub_llm)
    assert store.select("letters", eq={"application_id": app_id("N04")}) == []
    run = steps.latest_run(store, app_id("N04"))
    s2 = next(f for f in store.select("findings", eq={"run_id": run["id"]}) if store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"] == "S2")
    assert s2["ai_status"] == "Not met"
    review.review_finding(store, officer, s2["id"], action="confirm", reason="The CoE shows a 2027 start")
    drafts = store.select("letters", eq={"application_id": app_id("N04")})
    assert len(drafts) == 1 and drafts[0]["status"] == "draft" and drafts[0]["kind"] == "decline"
    body = drafts[0]["body_text"]
    assert "rule S2" in body and "What would change the outcome" in body and "How to ask for a review" in body
    assert "[PERSON" not in body and "[EMAIL" not in body and "[DOB" not in body                 # the applicant's words, not tokens
    assert not drafts[0].get("approved_at")
    with pytest.raises(Conflict, match="Sign off"):
        letters.update_letter(store, officer, drafts[0]["id"], approve=True)                  # released only after sign-off
    view = __import__("app.services.queue", fromlist=["x"]).applicant_view(store, applicant_actor, app_id("N01"))
    assert view["letters"] == []
    assert [a for a in store.select("audit_log") if a["action"] == "letter.generated"]


def test_an_edited_letter_is_not_overwritten_by_a_later_decision(store, officer, guided, stub_llm):
    to_decline(store, officer, guided, stub_llm)
    run = steps.latest_run(store, app_id("N04"))
    by = {store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"]: f for f in store.select("findings", eq={"run_id": run["id"]})}
    review.review_finding(store, officer, by["S2"]["id"], action="confirm", reason="Start date is in 2027")
    letter = store.select("letters", eq={"application_id": app_id("N04")})[0]
    letters.update_letter(store, officer, letter["id"], body_text=letter["body_text"] + "\nWe are sorry.")
    review.review_finding(store, officer, by["D5"]["id"], action="confirm", reason="The letter is dated 2023")
    ls = store.select("letters", eq={"application_id": app_id("N04")})
    assert len(ls) == 1 and ls[0]["status"] == "edited" and "We are sorry." in ls[0]["body_text"]


def test_the_outcome_names_each_unmet_rule_with_the_applicants_own_words_and_no_score(store, officer, guided, stub_llm):
    to_decline(store, officer, guided, stub_llm)
    decide_assessment(store, officer, "N04")
    out = outcome.build(store, officer, app_id("N04"), guided)
    assert out["result"] == "decline" and {u["rule_code"] for u in out["unmet"]} >= {"S2"}
    assert all(u["what_would_change"] for u in out["unmet"]) and out["letter"]["kind"] == "decline"
    keys = json.dumps(out).lower()
    assert not any(w in keys for w in ('"total', '"average', '"rank', '"score', "approve_all"))


def test_an_eligible_application_gets_a_panel_summary_and_a_next_steps_letter(store, officer, guided, stub_llm):
    to_step_3(store, officer, guided, stub_llm)
    decide_assessment(store, officer, "N01")
    out = outcome.build(store, officer, app_id("N01"), guided)
    assert out["result"] == "eligible" and out["unmet"] == [] and out["letter"] is None
    assert [m["rule_code"] for m in out["summary"]["marks"]] == ["M1", "M2", "M3", "M4", "M5"]
    assert all(set(m) == {"rule_code", "criterion", "mark", "not_assessed", "reason"} for m in out["summary"]["marks"])
    assert "added up" in out["summary"]["note"] and "total" not in json.dumps(out["summary"]).lower().replace("nothing is added up", "")
    letter = letters.generate_next_steps(store, officer, app_id("N01"), guided)
    assert letter["kind"] == "next_steps" and letter["status"] == "draft" and "What happens next" in letter["body_text"]
    with pytest.raises(Conflict, match="Sign off"):
        letters.update_letter(store, officer, letter["id"], approve=True)


def test_sign_off_needs_the_earlier_steps_and_every_rule_decided(store, officer, guided, stub_llm):
    confirm_documents(store, officer, settings=guided)
    with pytest.raises(SignOffBlocked, match="Documents and Redaction"):
        review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    to_step_3(store, officer, guided, stub_llm)
    # (steps 1 and 2 are done; one rule left undecided)
    decide_assessment(store, officer, "N01", skip={"S8"})
    assert status_of(store)[3] == "in_progress"
    with pytest.raises(SignOffBlocked) as e:
        review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    assert e.value.details["pending_rules"] == ["S8"]
    decide_assessment(store, officer, "N01")
    assert status_of(store)[3] == "done"
    with pytest.raises(Exception):
        review.sign_off(store, officer, app_id("N01"), statement_acknowledged=False)
    review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    assert status_of(store)[4] == "done" and app_of(store)["status"] == "signed_off"
    rec = outcome.build(store, officer, app_id("N01"), guided)["record"]
    assert rec["outcome"] == "eligible" and rec["version"] == 1 and rec["officer"] and rec["signed_at"] and len(rec["decisions"]) == 28
    assert [a for a in store.select("audit_log") if a["action"] == "application.signoff"]


def test_reopening_needs_a_reason_and_makes_the_next_sign_off_a_new_version(store, officer, guided, stub_llm):
    to_step_3(store, officer, guided, stub_llm)
    decide_assessment(store, officer, "N01")
    review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    with pytest.raises(ValidationFailed, match="reason"):
        review.reopen(store, officer, app_id("N01"), "  ")
    out = review.reopen(store, officer, app_id("N01"), "A document was added after sign-off")
    assert out["status"] == "in_review" and app_of(store)["status"] == "in_review"
    assert [a for a in store.select("audit_log") if a["action"] == "application.reopened"][0]["reason"] == "A document was added after sign-off"
    review.sign_off(store, officer, app_id("N01"), statement_acknowledged=True)
    rec = outcome.build(store, officer, app_id("N01"), guided)["record"]
    assert rec["version"] == 2 and rec["reopened"][0]["reason"] == "A document was added after sign-off"
    with pytest.raises(Exception):
        review.reopen(store, officer, app_id("N02"), "not signed off")
