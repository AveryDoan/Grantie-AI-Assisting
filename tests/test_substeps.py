"""Step 3 sub-steps: 3.1 Eligibility, 3.2 Consistency, (3.3 AI-use check), Merit. One backend source for every count.
Synthetic data only."""

from __future__ import annotations

from app.services import merit, review, steps, substeps
from app.store.base import one
from tests.test_merit_review import assessed, mark

CODES = ["M1", "M2", "M3", "M4", "M5"]


def step3(store, aid, settings):
    app = one(store.select("applications", eq={"id": aid}, limit=1))
    return steps.state(store, app, settings)["steps"][2]


def decide_every_rule(store, officer, aid):
    _, pending = review.undecided_rules(store, aid)
    for f in store.select("findings"):
        rule = one(store.select("rules", eq={"id": f["rule_id"]}, limit=1))
        if f["application_id"] == aid and rule["rule_code"] in pending and (rule.get("params") or {}).get("section") != "merit":
            if f["ai_status"] == "Evidence only":
                review.review_finding(store, officer, f["id"], action="override", final_status="Met", reason="Read the evidence myself")
            else:
                review.review_finding(store, officer, f["id"], action="confirm", reason="Checked against the documents")


def test_four_sub_steps_in_order_with_merit_last(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    subs = step3(store, aid, settings)["substeps"]
    assert [(s["number"], s["key"]) for s in subs] == [("3.1", "eligibility"), ("3.2", "consistency"), ("3.3", "merit")]
    # with an AI-use policy that the fairness gate switched on, the check is 3.3 and merit moves to 3.4
    pack = store.select("rule_packs", eq={"id": one(store.select("applications", eq={"id": aid}, limit=1))["rule_pack_id"]})[0]
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "ai_use_policy": "Only basic AI use.", "ai_check": {"enabled": True}}},
                 eq={"id": pack["id"]})
    subs = step3(store, aid, settings)["substeps"]
    assert [(s["number"], s["key"]) for s in subs] == [("3.1", "eligibility"), ("3.2", "consistency"), ("3.3", "ai_use"), ("3.4", "merit")]
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "ai_use_policy": "Only basic AI use.", "ai_check": {"enabled": False}}},
                 eq={"id": pack["id"]})
    assert "ai_use" not in [s["key"] for s in step3(store, aid, settings)["substeps"]]


def test_counts_agree_with_the_rule_engine_and_the_header(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    st = step3(store, aid, settings)
    subs = {s["key"]: s for s in st["substeps"]}
    _, pending = review.undecided_rules(store, aid)
    merit_pending = [c for c in pending if c in CODES]
    assert subs["eligibility"]["count"] == len(pending) - len(merit_pending)
    assert subs["merit"]["count"] == len(merit_pending)
    assert st["progress"]["total"] - st["progress"]["decided"] == subs["eligibility"]["count"] + subs["merit"]["count"]
    assert subs["eligibility"]["missing"].startswith(f"{subs['eligibility']['count']} eligibility")


def test_only_eligibility_and_merit_block_and_both_must_be_complete(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    decide_every_rule(store, officer, aid)
    st = step3(store, aid, settings)
    subs = {s["key"]: s for s in st["substeps"]}
    assert subs["eligibility"]["status"] == "done" and subs["merit"]["status"] == "in_progress"
    assert st["status"] != "done"                       # merit still open: Continue to Outcome stays off
    for code in CODES:
        mark(store, officer, aid, code)
    st = step3(store, aid, settings)
    assert st["status"] == "done"                       # consistency never opened: it does not block
    assert {s["key"]: s["status"] for s in st["substeps"]}["consistency"] == "not_started"


def test_opening_an_optional_sub_step_shows_reviewed_once_and_is_logged(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    app = one(store.select("applications", eq={"id": aid}, limit=1))
    substeps.mark_opened(store, officer, app, "consistency")
    substeps.mark_opened(store, officer, app, "consistency")
    assert {s["key"]: s["status"] for s in step3(store, aid, settings)["substeps"]}["consistency"] == "reviewed"
    logged = [a for a in store.select("audit_log") if a["action"] == "substep.opened"]
    assert len(logged) == 1 and logged[0]["details"] == {"substep": "consistency"}
    assert substeps.mark_opened(store, officer, app, "merit") == {"key": "merit", "opened": False}   # only optional ones


def test_a_sub_step_is_never_a_score(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    for s in step3(store, aid, settings)["substeps"]:
        assert not [k for k in s if any(w in k for w in ("score", "rank", "rating", "weight"))]
