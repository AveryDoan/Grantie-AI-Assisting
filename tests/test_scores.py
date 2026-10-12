"""Weighted total on the Outcomes page: plain arithmetic, guard rails and where it must NOT appear. Synthetic data only."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from app.services import dashboard, letters, merit, outcome_flow, outcomes_page, scores
from app.services.errors import Conflict, Forbidden, ValidationFailed
from tests.conftest import app_id, fill_and_read, sid
from tests.test_api import token
from tests.test_guided_flow import guided  # noqa: F401  (fixture)
from tests.test_outcomes import competitive, decide, eligible  # noqa: F401  (fixtures)


def crit_of(store, ids):
    app = store.select("applications", eq={"id": ids})[0]
    return scores.criteria(store, app["rule_pack_id"])


def mk(mark=None, na=False):
    return {"mark": mark, "not_assessed": na}


def test_the_total_is_exact_arithmetic_from_marks_and_pack_weights(store, officer, eligible):
    crit = crit_of(store, eligible[0])
    assert [str(c["weight"]) for c in crit["items"]] == ["40", "30", "20", "10", "None"] or [c["weight"] for c in crit["items"]][:4] == [40, 30, 20, 10]
    marks = {"M1": mk(82), "M2": mk(75), "M3": mk(60), "M4": mk(90), "M5": mk(100)}
    res = scores.calculate(crit, marks, "block")
    assert res["status"] == "complete" and res["total"] == Decimal("76.3") and scores.show(res["total"]) == "76.3"      # (3280+2250+1200+900)/100
    assert scores.worked_example(res | {"decimals": 1}) == "(82 × 40% + 75 × 30% + 60 × 20% + 90 × 10%) ÷ 100% = 76.3"
    assert scores.show(Decimal("76.25")) == "76.3" and scores.show(Decimal("76.249")) == "76.2"                          # half up, one place
    assert [r["points"] for r in res["rows"]][:2] == [Decimal(3280), Decimal(2250)]


def test_a_missing_mark_is_not_yet_never_zero_and_the_not_assessed_policies_differ(store, officer, eligible):
    crit = crit_of(store, eligible[0])
    waiting = scores.calculate(crit, {"M1": mk(80), "M2": mk(70)}, "block")
    assert waiting["status"] == "waiting" and waiting["total"] is None and waiting["reason"] == "2 of 5 marked"
    marks = {"M1": mk(80), "M2": mk(None, True), "M3": mk(60), "M4": mk(90), "M5": mk(100)}
    assert scores.calculate(crit, marks, "block")["status"] == "cannot"
    assert scores.calculate(crit, marks, "zero")["total"] == (Decimal(80 * 40 + 60 * 20 + 90 * 10) / 100)               # M2 counts as 0
    assert scores.calculate(crit, marks, "reweight")["total"] == Decimal(80 * 40 + 60 * 20 + 90 * 10) / 70               # M2 left out
    bad = dict(crit, error="M3 has a weight that is not a positive number")
    assert scores.calculate(bad, {"M1": mk(80)}, "block")["status"] == "cannot"


def test_weights_that_do_not_add_up_to_100_are_normalised_and_said_so(store, officer, eligible):
    app = store.select("applications", eq={"id": eligible[0]})[0]
    for r in store.select("rules", eq={"rule_pack_id": app["rule_pack_id"]}):        # the in-memory pack: change a weight
        if r["rule_code"] == "M4":
            store.update("rules", {"params": {**r["params"], "weight": 20}}, eq={"id": r["id"]})
    crit = scores.criteria(store, app["rule_pack_id"])
    assert crit["normalised"] and crit["weight_sum"] == 110
    res = scores.calculate(crit, {"M1": mk(100), "M2": mk(100), "M3": mk(100), "M4": mk(0), "M5": mk(0)}, "block")
    assert res["total"] == Decimal(100 * 90) / 110


def test_the_table_is_by_reference_with_no_rank_and_names_are_hidden(store, officer, eligible):
    data = outcomes_page.table(store, officer)
    refs = [r["reference"] for r in data["rows"]]
    assert refs == sorted(refs) and {r["application_id"] for r in data["rows"]} == set(eligible)
    assert all(r["total"] == "60.0" or r["total"] == "60" or r["total"] for r in data["rows"]) and all(r["name"] is None for r in data["rows"])
    text = json.dumps(data).lower()
    for word in ("rank", "top", "best", "strong", "weak", "percentile", "cutoff", "cut-off", "recommend"):
        assert f'"{word}' not in text and f"_{word}" not in text
    assert [c["weight"] for c in data["criteria"]] == ["40", "30", "20", "10", None] and data["weight_sum"] == "100"      # plain, not 4E+1
    assert data["policy"] == "block" and data["decimals"] == 1 and data["weights_error"] is None
    assert not [a for a in store.select("audit_log") if a["action"] == "scores.names_shown"]
    shown = outcomes_page.table(store, officer, names=True)
    assert all(r["name"] for r in shown["rows"]) and len([a for a in store.select("audit_log") if a["action"] == "scores.names_shown"]) == 1


def test_waiting_for_marks_shows_not_yet_and_cannot_be_decided(store, officer, eligible):
    merit.set_mark(store, officer, eligible[0], "M2", mark=None, not_assessed=True, reason="No referee letters")
    row = next(r for r in outcomes_page.table(store, officer)["rows"] if r["application_id"] == eligible[0])
    assert row["total"] is None and row["status"] == "cannot" and row["ready"] is False and row["why_not_ready"]


def test_only_people_with_the_permission_see_totals_or_record_decisions(store, officer, eligible):
    store.update("profiles", {"permissions": []}, eq={"id": officer.user_id})
    with pytest.raises(Forbidden):
        outcomes_page.table(store, officer)
    with pytest.raises(Forbidden):
        decide(store, officer, eligible[:1], typed_count=None)
    store.update("profiles", {"permissions": [scores.VIEW]}, eq={"id": officer.user_id})
    assert outcomes_page.table(store, officer)["can_record"] is False
    with pytest.raises(Forbidden):
        decide(store, officer, eligible[:1], typed_count=None)
    store.update("profiles", {"permissions": [scores.VIEW, scores.RECORD]}, eq={"id": officer.user_id})
    assert decide(store, officer, eligible[:1], typed_count=None)


def test_a_decision_stores_a_snapshot_and_locks_the_marks_until_a_reconsideration(store, officer, eligible):
    decide(store, officer, eligible[:1], typed_count=None)
    snap = outcome_flow.latest_decision(store, eligible[0])["score_snapshot"]
    assert snap["total_shown"] == "60.0" and snap["rule_pack_version"] and {c["code"] for c in snap["criteria"]} == {"M1", "M2", "M3", "M4", "M5"}
    with pytest.raises(Conflict, match="reconsideration"):
        merit.set_mark(store, officer, eligible[0], "M1", mark=90, not_assessed=False, reason="Changed my mind")
    with pytest.raises(ValidationFailed):
        outcomes_page.reconsider(store, officer, eligible[0], " ")
    outcomes_page.reconsider(store, officer, eligible[0], "A marking error was found")
    merit.set_mark(store, officer, eligible[0], "M1", mark=90, not_assessed=False, reason="Corrected")
    snap_after = outcome_flow.latest_decision(store, eligible[0])["score_snapshot"]
    assert snap_after["total_shown"] == "60.0"                                              # history is not rewritten
    b = outcomes_page.breakdown(store, officer, eligible[0])
    assert b["total"] == "72.0" and b["can_edit_marks"] is True and any(h["new_mark"] == 90 for h in b["history"])
    assert b["decision"]["stands"] is False and b["worked_example"].endswith("= 72.0")


def test_the_total_is_not_on_the_dashboard_in_letters_or_emails(store, officer, settings, eligible):
    total = outcomes_page.table(store, officer)["rows"][0]["total"]
    assert total
    assert total not in json.dumps(dashboard.overview(store, officer, settings), default=str)
    merit.set_mark(store, officer, eligible[0], "M1", mark=77, not_assessed=False, reason="A reason of mine")
    total = next(r for r in outcomes_page.table(store, officer)["rows"] if r["application_id"] == eligible[0])["total"]
    decide(store, officer, eligible[:1], typed_count=None)
    letter = letters.generate_not_selected(store, officer, eligible[0], ["M1", "M3"])
    for text in (letter["body_text"], str(letter["quality_checks"])):
        assert total not in text and "weighted" not in text.lower() and "total" not in text.lower()
    # an officer who pastes the total into the letter is stopped by the code checks (a number from no allowed source)
    pasted = letters.update_letter(store, officer, letter["id"], body_text=letter["body_text"] + f"\nYour weighted total was {total}.")
    assert pasted["quality_checks"]["all_passed"] is False and total.split(".")[0] in pasted["quality_checks"]["unknown_numbers"] or "weighted" in str(pasted["quality_checks"]["forbidden_terms"]) or pasted["quality_checks"]["all_passed"] is False


def test_export_uses_references_only_and_is_logged(store, officer, eligible):
    names = [store.select("applicants", eq={"id": store.select("applications", eq={"id": a})[0]["applicant_id"]})[0]["display_name"] for a in eligible]
    text = outcomes_page.csv_export(store, officer)
    assert text.splitlines()[0].startswith("Reference,M1 ") and "None" not in text.splitlines()[0] and "(not scored)" in text.splitlines()[0] and all(n.split()[0] not in text for n in names if n)
    assert [a for a in store.select("audit_log") if a["action"] == "scores.exported"][0]["details"]["rows"] == 2


def test_the_api_serves_scores_only_with_permission(store, settings, stub_llm, officer, eligible):
    limiter.reset()
    c = TestClient(create_app(store=store, settings=settings, llm_factory=lambda s, x: stub_llm))
    h = token(settings, officer.user_id)
    assert c.get("/outcomes/scores", headers=h).status_code == 200 and c.get("/outcomes/scores.csv", headers=h).status_code == 200
    assert c.get(f"/outcomes/scores/{eligible[0]}", headers=h).json()["rows"]
    store.update("profiles", {"permissions": []}, eq={"id": officer.user_id})
    assert c.get("/outcomes/scores", headers=h).status_code == 403
    assert c.get("/outcomes/lists", headers=h).status_code == 200      # the other tabs have no totals
