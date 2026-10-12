"""Ineligible path, round decisions, sign-off and release (outcomes brief, tests 1-5 and 7-16). Synthetic data only.
Database-level versions of 5, 6, 7 and 14 are in tests/test_outcome_database.py."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from app.pipeline.orchestrator import run_assessment
from app.services import ai_use, letter_checks, letters, merit, outcome, outcome_flow, review, steps
from app.services.errors import Conflict, ValidationFailed
from seed.data import SNT_PACK
from tests.conftest import app_id, fill_and_read, fill_placeholders
from tests.test_guided_flow import app_of, decide_assessment, guided, to_decline, to_step_3  # noqa: F401  (guided is a fixture)

ROOT = Path(__file__).resolve().parents[1]
CRITERIA = ["M1", "M2", "M3", "M4", "M5"]


def by_code(store, code="N04"):
    run = steps.latest_run(store, app_id(code))
    return {store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"]: f for f in store.select("findings", eq={"run_id": run["id"]})}


def decline_letter(store, code="N04"):
    return letters.latest_of(store, app_id(code), "decline")


@pytest.fixture
def ineligible(store, officer, guided, stub_llm):  # noqa: F811
    """N04 after Step 2, with S2 confirmed Not met by the officer."""
    to_decline(store, officer, guided, stub_llm)
    review.review_finding(store, officer, by_code(store)["S2"]["id"], action="confirm", reason="The CoE shows a 2027 start")
    return app_id("N04")


@pytest.fixture
def competitive(store):
    pack = store.select("rule_packs", eq={"id": SNT_PACK})[0]
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "competitive_round": True}}, eq={"id": SNT_PACK})


@pytest.fixture
def eligible(store, officer, guided, stub_llm, competitive):  # noqa: F811
    """N01 and N08: every eligibility rule Met by the officer, every merit criterion marked with a reason."""
    for code in ("N01", "N08"):
        to_step_3(store, officer, guided, stub_llm, code)
        decide_assessment(store, officer, code)
    return [app_id("N01"), app_id("N08")]


def decide(store, officer, ids, decision="not_selected_competitive", **kw):
    args = dict(decided_by="Assessment panel", authority="Panel chair", decided_at="2026-10-14", reference="Panel minute 2026/7",
                reason_note="Funding was limited; the panel recommended other applications for the places available.",
                typed_count=len(ids)) | kw
    return outcome_flow.record_decisions(store, officer, ids, decision=decision, **args)


# ------------------------------------------------------------------ 1. Go to Outcome now: only after an officer's Not met

def test_go_to_outcome_needs_an_officer_confirmed_not_met(store, officer, guided, stub_llm):  # noqa: F811
    to_decline(store, officer, guided, stub_llm)
    assert by_code(store)["S2"]["ai_status"] == "Not met"          # the AI suggested Not met...
    with pytest.raises(Conflict, match="Confirm a rule as Not met first"):
        outcome_flow.go_to_outcome(store, officer, app_id("N04"), continue_anyway=True)
    assert store.select("steps_not_assessed") == [] and not outcome_flow.on_ineligible_path(store, app_id("N04"))
    review.review_finding(store, officer, by_code(store)["S2"]["id"], action="confirm", reason="The CoE shows a 2027 start")
    assert outcome_flow.go_to_outcome(store, officer, app_id("N04"), continue_anyway=True)["not_met_rules"] == ["S2"]


def test_no_ai_or_pipeline_code_can_move_an_application_to_outcome():
    callers = [p for p in (ROOT / "app").rglob("*.py") if "go_to_outcome(" in p.read_text(encoding="utf-8")]
    assert sorted(p.relative_to(ROOT).as_posix() for p in callers) == ["app/main.py", "app/services/outcome_flow.py"]
    for p in list((ROOT / "app/pipeline").rglob("*.py")) + list((ROOT / "app/llm").rglob("*.py")):
        assert "steps_not_assessed" not in p.read_text(encoding="utf-8") and "go_to_outcome" not in p.read_text(encoding="utf-8")


def test_open_findings_warn_first_and_the_officer_chooses(store, officer, ineligible):
    open_rules = outcome_flow.could_change(store, ineligible)
    if open_rules:
        with pytest.raises(Conflict, match="could change this") as e:
            outcome_flow.go_to_outcome(store, officer, ineligible)
        assert e.value.details["could_change"] == open_rules
    out = outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    assert {"consistency", "merit"} <= set(out["not_assessed"])
    assert [a for a in store.select("audit_log") if a["action"] == "outcome.go_now"][0]["details"]["could_change_acknowledged"] == open_rules


# ------------------------------------------------------------------ 2. not assessed (ineligible), audited; merit and AI-use never run

def test_going_to_outcome_records_not_assessed_steps_and_stops_merit_and_ai_use(store, officer, ineligible, guided, stub_llm, monkeypatch):  # noqa: F811
    outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    st = steps.state(store, app_of(store, "N04"), guided)
    subs = {s["key"]: s for s in st["steps"][2]["substeps"]}
    assert st["steps"][2]["status"] == "done" and st["current"] == 4
    assert subs["consistency"]["status"] == subs["merit"]["status"] == "not_assessed"
    assert subs["merit"]["label"] == "Not assessed (ineligible)"
    entry = [a for a in store.select("audit_log") if a["action"] == "outcome.go_now"][0]
    assert entry["actor_id"] == officer.user_id and entry["details"]["not_met_rules"] == ["S2"]
    assert {"consistency", "merit"} <= set(entry["details"]["not_assessed"])
    with pytest.raises(Conflict, match="Not assessed"):
        merit.set_mark(store, officer, ineligible, "M1", mark=50, not_assessed=False, reason="x")
    with pytest.raises(Conflict, match="Not assessed"):
        outcome_flow.require_assessable(store, ineligible, "ai_use")
    # a new assessment run does not run merit extraction for it
    calls = []
    monkeypatch.setattr("app.services.merit_summary.generate", lambda *a, **k: calls.append(1))
    run_assessment(store, officer, ineligible, stub_llm, guided)
    assert calls == []


def test_the_ai_use_check_refuses_to_run(store, officer, ineligible):
    outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    store.insert("steps_not_assessed", {"application_id": ineligible, "step": "ai_use", "reason": outcome_flow.NOT_ASSESSED, "officer_id": officer.user_id})
    with pytest.raises(Conflict, match="Not assessed"):
        ai_use.run_check(store, officer, ineligible, scorer=lambda texts: [0.0 for _ in texts])


# ------------------------------------------------------------------ 3 and 4. the letter lists exactly the confirmed Not met findings

def test_the_letter_lists_exactly_the_confirmed_not_met_findings_and_follows_reversals(store, officer, ineligible):
    f = by_code(store)
    first = decline_letter(store)
    assert re.findall(r"^Reason \d+: rule (\S+)", first["body_text"], re.M) == ["S2"]
    assert first["source_finding_ids"] == [f["S2"]["id"]]
    # another Not met: redrafted with both, in rule order
    other = next(c for c, x in f.items() if c not in ("S2",) and x["ai_status"] != "Evidence only" and x["is_valid"] and not c.startswith("M"))
    review.review_finding(store, officer, f[other]["id"], action="override", final_status="Not met", reason="Officer read the document")
    second = decline_letter(store)
    assert second["version"] > first["version"]
    assert set(re.findall(r"^Reason \d+: rule (\S+)", second["body_text"], re.M)) == {"S2", other}
    assert [a for a in store.select("audit_log") if a["action"] == "letter.redrafted"]
    # reverse it: its paragraph goes, and the letter is redrafted
    review.review_finding(store, officer, f[other]["id"], action="override", final_status="Met", reason="Checked again: it is met")
    third = decline_letter(store)
    assert re.findall(r"^Reason \d+: rule (\S+)", third["body_text"], re.M) == ["S2"] and f"rule {other}" not in third["body_text"]


def test_needs_evidence_is_never_a_reason_in_the_decline_letter(store, officer, ineligible):
    f = by_code(store)
    target = next(c for c, x in f.items() if c != "S2" and x["is_valid"] and x["ai_status"] != "Evidence only" and not c.startswith("M"))
    review.review_finding(store, officer, f[target]["id"], action="override", final_status="Needs evidence", reason="Ask for the CoE")
    assert f"rule {target}" not in decline_letter(store)["body_text"]


def test_reversing_the_only_not_met_clears_the_path(store, officer, ineligible):
    outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    review.review_finding(store, officer, by_code(store)["S2"]["id"], action="override", final_status="Met", reason="Wrong year read")
    assert not outcome_flow.on_ineligible_path(store, ineligible)
    assert all(r["cleared_at"] for r in store.select("steps_not_assessed"))
    assert [a for a in store.select("audit_log") if a["action"] == "outcome.path_cleared"]
    assert app_of(store, "N04")["eligibility_result"] is None


# ------------------------------------------------------------------ 5. sign-off conditions

def test_sign_off_needs_a_viewed_letter_passing_checks_and_a_confirmed_not_met(store, officer, ineligible):
    outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    with pytest.raises(Conflict, match="Review the letter first"):
        outcome_flow.sign_off_and_release(store, officer, ineligible, statement_acknowledged=True)
    letter = decline_letter(store)
    letters.mark_viewed(store, officer, letter["id"])
    with pytest.raises(Conflict, match="placeholders"):                         # seed settings leave [placeholders]
        outcome_flow.sign_off_and_release(store, officer, ineligible, statement_acknowledged=True)
    letters.update_letter(store, officer, letter["id"], body_text=fill_placeholders(letter["body_text"]) + "\nYou ranked lower than other applicants.")
    letters.mark_viewed(store, officer, letter["id"])
    with pytest.raises(Conflict, match="every check passes"):
        outcome_flow.sign_off_and_release(store, officer, ineligible, statement_acknowledged=True)
    fill_and_read(store, officer, letter["id"])
    letter = letters.update_letter(store, officer, letter["id"], body_text=fill_placeholders(letter["body_text"]))
    with pytest.raises(Conflict, match="Review the letter first"):               # an edit has to be read again
        outcome_flow.sign_off_and_release(store, officer, ineligible, statement_acknowledged=True)
    letters.mark_viewed(store, officer, letter["id"])
    out = outcome_flow.sign_off_and_release(store, officer, ineligible, statement_acknowledged=True)
    assert out["outcome_status"] == "ineligible" and out["letter"]["released_at"] and out["letter"]["status"] == "approved"
    app = app_of(store, "N04")
    assert app["status"] == "signed_off" and app["outcome_status"] == "ineligible" and app["eligibility_result"] == "ineligible"
    assert {"outcome.signed_off", "letter.released", "letter.viewed", "application.signoff"} <= {a["action"] for a in store.select("audit_log")}


def test_no_confirmed_not_met_means_no_decline_sign_off(store, officer, guided, stub_llm):  # noqa: F811
    to_decline(store, officer, guided, stub_llm)
    cond = outcome_flow.sign_off_conditions(store, officer, app_of(store, "N04"))
    assert not cond["ready"] and cond["kind"] is None


# ------------------------------------------------------------------ 7, 8, 9 and 10. round decisions

def test_round_decisions_only_for_eligible_applications_with_complete_merit(store, officer, ineligible, competitive):
    with pytest.raises(Conflict, match="ready"):
        decide(store, officer, [ineligible])


def test_incomplete_merit_is_not_ready(store, officer, guided, stub_llm, competitive):  # noqa: F811
    to_step_3(store, officer, guided, stub_llm, "N01")
    decide_assessment(store, officer, "N01", skip={"M3"})
    lst = outcome_flow.round_list(store, officer)
    row = next(r for r in lst["not_ready"] if r["application_id"] == app_id("N01"))
    assert any("M3" in w for w in row["why"])
    with pytest.raises(Conflict):
        decide(store, officer, [app_id("N01")])


def test_the_round_list_is_by_reference_with_no_marks_and_no_select_all(store, officer, eligible):
    lst = outcome_flow.round_list(store, officer)
    refs = [r["reference"] for r in lst["ready"]]
    assert refs == sorted(refs) and {r["application_id"] for r in lst["ready"]} == set(eligible)
    text = str(lst).lower()
    for word in ("mark", "score", "total", "average", "rank", "select_all"):
        assert f"'{word}" not in text
    assert "no ranking is shown here" in lst["note"].lower()


def test_multi_select_needs_the_typed_count_and_a_reference(store, officer, eligible):
    with pytest.raises(ValidationFailed, match="Type the number"):
        decide(store, officer, eligible, typed_count=None)
    with pytest.raises(ValidationFailed, match="Type the number"):
        decide(store, officer, eligible, typed_count=3)
    with pytest.raises(ValidationFailed, match="reference"):
        decide(store, officer, eligible, reference="  ")
    with pytest.raises(ValidationFailed, match="reason note"):
        decide(store, officer, eligible, reason_note="Too short")
    assert store.select("round_decisions") == []
    rows = decide(store, officer, eligible)
    assert len(rows) == 2 and all(r["created_by"] == officer.user_id for r in rows)
    assert len([a for a in store.select("audit_log") if a["action"] == "round.decision"]) == 2
    # saving releases nothing: draft not-selected letters only
    drafted = [l for l in store.select("letters") if l["kind"] == "not_selected"]
    assert len(drafted) == 2 and not any(l.get("released_at") for l in drafted)


def test_a_not_selected_letter_needs_the_decision_and_its_reason_note(store, officer, eligible):
    with pytest.raises(Conflict, match="round decision"):
        letters.generate_not_selected(store, officer, eligible[0], [])
    decide(store, officer, eligible[:1], typed_count=None)
    letter = letters.generate_not_selected(store, officer, eligible[0], ["M1", "M3"])
    body = letter["body_text"]
    assert body.splitlines()[4].startswith("Thank you for applying for") and "was not selected for funding in this round" in body
    assert "Your application met the eligibility requirements." in body
    assert body.count("\n- ") == 2 and "Read it." in body                    # the officer's own reasons, tidied
    assert "out of 100" not in body                                          # include_marks_in_letter is off by default


def test_marks_appear_only_when_the_agency_turns_them_on(store, officer, eligible):
    pack = store.select("rule_packs", eq={"id": SNT_PACK})[0]
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "include_marks_in_letter": True}}, eq={"id": SNT_PACK})
    decide(store, officer, eligible[:1], typed_count=None)
    body = letters.generate_not_selected(store, officer, eligible[0], ["M1"])["body_text"]
    assert "Your mark for this criterion: 60 out of 100." in body and "total" not in body.lower()


def test_eligible_competitive_round_needs_a_round_decision_before_sign_off(store, officer, eligible):
    out = outcome.build(store, officer, eligible[0])
    assert out["awaiting_round_decision"] is True and out["conditions"]["ready"] is False
    decide(store, officer, eligible[:1], decision="selected", typed_count=None, reason_note=None)
    out = outcome.build(store, officer, eligible[0])
    assert out["letter_kind"] == "next_steps" and out["letter"] and out["round_decision"]["decision"] == "selected"


# ------------------------------------------------------------------ 11 and 12. letter safety

ADVERSARIAL = ["ranked", "score of", "lower than", "other applicants", "weaker", "stronger", "percentile", "fraud", "cheating",
               "suspicious", "AI-generated", "detector", "redacted", "better than", "consistency signal", "AI-use check",
               "your grammar", "spelling mistakes"]


def test_forbidden_language_and_invented_numbers_always_fail_the_checks():
    rnd = random.Random(7)
    base = "Dear applicant,\nThank you for applying.\nContact: the grants team\n"
    for _ in range(300):
        bad = rnd.choice(ADVERSARIAL)
        filler = " ".join(rnd.choice(["your", "application", "was", "read", "by", "an", "officer", "and"]) for _ in range(rnd.randint(0, 8)))
        variants = [bad, bad.upper(), bad.title()]
        body = base + f"{filler} {rnd.choice(variants)} {filler}."
        c = letter_checks.check(body, allowed_sources=[])
        assert not (c["no_forbidden_terms"] and c["no_style_criticism"]), body
    for n in ("17", "250", "3,000", "99.5"):
        c = letter_checks.check(base + f"There were {n} applications this round.", allowed_sources=["Round 1 2026"])
        assert c["unknown_numbers"] and not c["numbers_from_allowed_sources"]
    ok = letter_checks.check(base + "This is Round 1 2026.", allowed_sources=["Round 1 2026"])
    assert ok["numbers_from_allowed_sources"] and ok["no_forbidden_terms"]


def test_an_officer_reason_with_comparison_language_blocks_the_not_selected_letter(store, officer, eligible):
    merit.set_mark(store, officer, eligible[0], "M2", mark=40, not_assessed=False, reason="Weaker than other applicants in the round")
    decide(store, officer, eligible[:1], typed_count=None)
    letter = letters.generate_not_selected(store, officer, eligible[0], ["M2"])
    assert letter["quality_checks"]["all_passed"] is False and "weaker" in letter["quality_checks"]["forbidden_terms"]


def test_ai_use_consistency_and_redaction_details_never_reach_a_letter(store, officer, ineligible):
    # signals present on the application
    store.insert("consistency_flags", {"application_id": ineligible, "check_id": "x", "check_type": "timeline", "strength": "strong",
                                       "description": "Dates on the CoE and the form differ", "status": "confirmed", "verification": "verified"})
    store.insert("ai_use_checks", {"application_id": ineligible, "model": "detector-model", "revision": "r", "thresholds": {},
                                   "min_words": 80, "fields": [{"field": "leadership", "result": "Stronger signal"}]})
    body = decline_letter(store)["body_text"].lower()
    for word in ("dates on the coe and the form differ", "consistency", "stronger signal", "detector", "ai-use", "redact", "[person", "[email"):
        assert word not in body


# ------------------------------------------------------------------ 13 and 14. batch release; frozen after release

def _signed_held(store, officer, aid):
    letter = letters.latest_of(store, aid, "not_selected")
    fill_and_read(store, officer, letter["id"])
    outcome_flow.sign_off_and_release(store, officer, aid, statement_acknowledged=True, release_now=False)
    return letters.latest_of(store, aid, "not_selected")


def test_batch_release_only_letters_this_officer_opened_and_typed_count(store, officer, eligible):
    from tests.conftest import sid

    decide(store, officer, eligible)
    held = [_signed_held(store, officer, aid) for aid in eligible]
    assert all(l["signed_off_at"] and not l["released_at"] for l in held)
    ids = [l["id"] for l in held]
    with pytest.raises(ValidationFailed, match="Type the number"):
        outcome_flow.release_many(store, officer, ids, typed_count=1)
    # a colleague who has not opened them cannot release them
    other = type(officer)(user_id=sid("user:colleague"), role="officer", organisation_id=officer.organisation_id)
    store.insert("profiles", {"id": other.user_id, "role": "officer", "organisation_id": officer.organisation_id})
    with pytest.raises(Conflict, match="Open and read each letter yourself"):
        outcome_flow.release_many(store, other, ids, typed_count=2)
    released = outcome_flow.release_many(store, officer, ids, typed_count=2)
    assert all(l["released_at"] and l["status"] == "approved" for l in released)


def test_after_release_edits_are_rejected_and_only_a_reconsideration_reopens(store, officer, eligible):
    decide(store, officer, eligible[:1], typed_count=None)
    letter = _signed_held(store, officer, eligible[0])
    outcome_flow.release_many(store, officer, [letter["id"]], typed_count=1)
    with pytest.raises(Conflict, match="released"):
        letters.update_letter(store, officer, letter["id"], body_text="changed")
    with pytest.raises(Conflict):
        decide(store, officer, eligible[:1], decision="selected", typed_count=None, reason_note=None)
    review.reopen(store, officer, eligible[0], "The applicant asked for a review")
    assert [a for a in store.select("audit_log") if a["action"] == "application.reopened"][0]["details"]["reconsideration"] is True
    decide(store, officer, eligible[:1], decision="selected", typed_count=None, reason_note=None)
    assert outcome_flow.latest_decision(store, eligible[0])["version"] == 2


# ------------------------------------------------------------------ 15 and 16. twins; no model in the letter path

def test_same_facts_in_three_writing_styles_give_the_same_letter_reasons(store, officer, stub_llm, settings):
    reasons = []
    for code in ("C04", "C05", "C06"):               # family C: polished, plain, second-language English
        run_assessment(store, officer, app_id(code), stub_llm, settings)
        for c, f in by_code(store, code).items():
            if f["ai_status"] == "Not met" and f["is_valid"]:
                review.review_finding(store, officer, f["id"], action="confirm", reason=f"Officer checked rule {c} against the records")
            elif f["ai_status"] == "Evidence only" or not f["is_valid"]:
                review.review_finding(store, officer, f["id"], action="override", final_status="Met", reason="Officer judgement")
            else:
                review.review_finding(store, officer, f["id"], action="confirm")
        body = letters.generate_letter(store, officer, app_id(code))["body_text"]
        reasons.append([line for line in body.splitlines()
                        if line.startswith(("Reason ", "What the rule requires", "Why this did not meet", "What would change"))])
    assert reasons[0] and reasons[0] == reasons[1] == reasons[2]


def test_letters_are_built_by_code_so_a_model_failure_cannot_block_sign_off(store, officer, ineligible, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("app.llm.client.LLMClient.complete", boom, raising=False)
    outcome_flow.go_to_outcome(store, officer, ineligible, continue_anyway=True)
    letter = letters.generate_letter(store, officer, ineligible)
    assert letter["template"] is True and letter["body_text"].count("Reason 1: rule S2") == 1
    src = (ROOT / "app/services/letters.py").read_text(encoding="utf-8")
    assert "llm" not in src.lower().replace("no language model", "")
