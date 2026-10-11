"""Merit marks (the officer's own 0 to 100), sign-off gating, the audit log, summary bullets linked to their source passages,
linked applications, and the consistency overview. Every case is synthetic."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.domain import Finding, Rule
from app.main import create_app
from app.pipeline.orchestrator import run_assessment
from app.pipeline.verification import verify_findings
from app.services import linked, merit, queue as queue_service, review
from app.services.access import Actor
from app.services.errors import Conflict, Forbidden, NotFound, SignOffBlocked, ValidationFailed
from seed import data
from tests.conftest import app_id
from tests.test_api import token

CODES = ["M1", "M2", "M3", "M4", "M5"]


def assessed(store, officer, stub_llm, settings, code="N04"):
    run_assessment(store, officer, app_id(code), stub_llm, settings)
    return app_id(code)


def mark(store, officer, aid, code, value=70, reason="Read the answer"):
    return merit.set_mark(store, officer, aid, code, mark=value, not_assessed=False, reason=reason)


# ------------------------------------------------------------------ marking rules


def test_a_mark_starts_empty_and_the_ai_never_fills_it(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    assert store.select("merit_marks") == []
    detail = queue_service.application_detail(store, officer, aid, settings)
    assert [f["merit_mark"] for f in detail["findings"] if f["section"] == "merit"] == [None] * 5
    assert all(f["merit_mark"] is None for f in detail["findings"] if f["section"] != "merit")


def test_a_mark_needs_a_reason(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    for reason in (None, "", "   "):
        with pytest.raises(ValidationFailed, match="reason"):
            merit.set_mark(store, officer, aid, "M1", mark=70, not_assessed=False, reason=reason)
    assert store.select("merit_marks") == []


@pytest.mark.parametrize("bad", [-1, 101, 1000, 50.5, True, "70", None])
def test_a_mark_is_a_whole_number_from_0_to_100(store, officer, stub_llm, settings, bad):
    aid = assessed(store, officer, stub_llm, settings)
    with pytest.raises(ValidationFailed):
        merit.set_mark(store, officer, aid, "M1", mark=bad, not_assessed=False, reason="because")


def test_zero_and_one_hundred_are_valid_marks(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    assert mark(store, officer, aid, "M1", 0)["mark"] == 0
    assert mark(store, officer, aid, "M3", 100)["mark"] == 100


def test_not_assessed_needs_no_mark_and_cannot_be_combined_with_one(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    row = merit.set_mark(store, officer, aid, "M2", mark=None, not_assessed=True, reason=None)
    assert row["not_assessed"] is True and row["mark"] is None
    with pytest.raises(ValidationFailed, match="not both"):
        merit.set_mark(store, officer, aid, "M3", mark=50, not_assessed=True, reason="x")


def test_unknown_criterion_applicant_other_organisation_and_signed_off(store, officer, other_officer, applicant_actor, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    with pytest.raises(NotFound):
        merit.set_mark(store, officer, aid, "S1", mark=50, not_assessed=False, reason="x")     # an eligibility rule, not a merit criterion
    with pytest.raises(Forbidden):
        merit.set_mark(store, applicant_actor, aid, "M1", mark=50, not_assessed=False, reason="x")
    with pytest.raises(NotFound):
        merit.set_mark(store, other_officer, aid, "M1", mark=50, not_assessed=False, reason="x")


# ------------------------------------------------------------------ the audit log


def test_every_change_is_audited_with_officer_time_old_and_new_value(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    mark(store, officer, aid, "M1", 60, "First look")
    mark(store, officer, aid, "M1", 75, "Read the transcript too")
    merit.set_mark(store, officer, aid, "M1", mark=None, not_assessed=True, reason=None)
    rows = [r for r in store.select("audit_log") if r["action"] == "merit.mark"]
    assert len(rows) == 3
    assert [(r["details"]["old_mark"], r["details"]["new_mark"]) for r in rows] == [(None, 60), (60, 75), (75, None)]
    assert [(r["details"]["old_not_assessed"], r["details"]["new_not_assessed"]) for r in rows][-1] == (False, True)
    assert all(r["actor_id"] == officer.user_id and r["actor_role"] == "officer" and r["occurred_at"] and r["application_id"] == aid for r in rows)
    assert rows[0]["reason"] == "First look" and rows[0]["details"]["rule_code"] == "M1"
    # counts and codes only: no application text in the details
    assert "prize" not in json.dumps([r["details"] for r in rows]).lower()


def test_saving_the_same_mark_again_records_nothing(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    mark(store, officer, aid, "M1", 60, "Same")
    mark(store, officer, aid, "M1", 60, "Same")
    assert len([r for r in store.select("audit_log") if r["action"] == "merit.mark"]) == 1


# ------------------------------------------------------------------ sign-off


def decide_everything_but_merit(store, officer, findings):
    for code, f in findings.items():
        if code in CODES:
            continue
        if f["ai_status"] == "Evidence only" or not f["is_valid"]:
            review.review_finding(store, officer, f["id"], action="override", final_status="Met", reason="Officer read the evidence")
        elif f["ai_status"] == "Not met":
            review.review_finding(store, officer, f["id"], action="confirm", reason="Officer checked")
        else:
            review.review_finding(store, officer, f["id"], action="confirm")


def test_sign_off_is_blocked_until_every_criterion_is_marked_or_not_assessed(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    run = store.select("assessment_runs", eq={"application_id": aid})[0]
    findings = {store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"]: f for f in store.select("findings", eq={"run_id": run["id"]})}
    decide_everything_but_merit(store, officer, findings)
    with pytest.raises(SignOffBlocked) as e:
        review.sign_off(store, officer, aid, statement_acknowledged=True)
    assert e.value.details["pending_rules"] == CODES
    for code in ("M1", "M2", "M3"):
        mark(store, officer, aid, code, 55)
    merit.set_mark(store, officer, aid, "M4", mark=None, not_assessed=True, reason=None)
    with pytest.raises(SignOffBlocked) as e:
        review.sign_off(store, officer, aid, statement_acknowledged=True)
    assert e.value.details["pending_rules"] == ["M5"]       # one criterion left
    # a status review is not a mark
    review.review_finding(store, officer, findings["M5"]["id"], action="override", final_status="Met", reason="Officer judgement")
    with pytest.raises(SignOffBlocked):
        review.sign_off(store, officer, aid, statement_acknowledged=True)
    mark(store, officer, aid, "M5", 80)
    review.sign_off(store, officer, aid, statement_acknowledged=True)
    with pytest.raises(Conflict):
        mark(store, officer, aid, "M1", 10)                 # locked after sign-off


def test_the_queue_counts_an_unmarked_criterion_as_open_work(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    before = next(q for q in queue_service.list_queue(store, officer, settings) if q["id"] == aid)["attention"]["unreviewed_findings"]
    mark(store, officer, aid, "M1")
    after = next(q for q in queue_service.list_queue(store, officer, settings) if q["id"] == aid)["attention"]["unreviewed_findings"]
    assert before - after == 1


# ------------------------------------------------------------------ no total, average, weighted score or rank


def test_nothing_is_totalled_or_compared(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    for code in CODES:
        mark(store, officer, aid, code, 10 * (CODES.index(code) + 5))
    detail = queue_service.application_detail(store, officer, aid, settings)
    def keys(node):
        if isinstance(node, dict):
            return set(node) | set().union(*(keys(v) for v in node.values())) if node else set()
        return set().union(*(keys(v) for v in node)) if isinstance(node, list) and node else set()

    names = {k.lower() for k in keys(detail)}
    assert not [k for k in names if any(w in k for w in ("total", "average", "weighted", "overall", "rank", "percentile", "mean"))]
    assert len(store.select("merit_marks", eq={"application_id": aid})) == 5   # one row per criterion, nothing summed
    assert all(set(m) >= {"mark", "not_assessed", "reason"} and "total" not in m for m in store.select("merit_marks"))


def test_the_merit_weights_are_not_applied(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    mark(store, officer, aid, "M1", 100)
    mark(store, officer, aid, "M4", 0)
    detail = queue_service.application_detail(store, officer, aid, settings)
    marks = {f["rule_code"]: f["merit_mark"]["mark"] for f in detail["findings"] if f["merit_mark"]}
    assert marks == {"M1": 100, "M4": 0}       # exactly what the officer typed, whatever the rule's weight


def test_the_api_validates_and_audits(store, settings, stub_llm, officer):
    limiter.reset()
    client = TestClient(create_app(store=store, settings=settings, llm_factory=lambda s, c: stub_llm))
    h = token(settings, officer.user_id)
    aid = app_id("N04")
    assert client.post(f"/applications/{aid}/assess", headers=h, json={}).status_code == 200
    url = f"/applications/{aid}/merit-marks/M1"
    assert client.put(url, headers=h, json={"mark": 70}).status_code == 422                       # no reason
    assert client.put(url, headers=h, json={"mark": 101, "reason": "x"}).status_code == 422
    assert client.put(url, headers=h, json={"mark": 50.5, "reason": "x"}).status_code == 422
    assert client.put(url, headers=h, json={"mark": True, "reason": "x"}).status_code == 422
    ok = client.put(url, headers=h, json={"mark": 72, "reason": "Clear evidence in the answer"})
    assert ok.status_code == 200 and ok.json()["mark"] == 72
    assert client.put(url, headers=h, json={"mark": None, "not_assessed": True}).json()["not_assessed"] is True
    assert len([r for r in store.select("audit_log") if r["action"] == "merit.mark"]) == 2


# ------------------------------------------------------------------ summary bullets and their source passages

SOURCE = "I lead the first-aid club of nine students. Every Saturday I volunteer at a community health centre."
LETTER = "Referee letter body. She helped every patient who came to the clinic and trained new volunteers."


def verified(summaries, **kw):
    f = Finding(rule_id="r", rule_code="M3", ai_status="Evidence only", check_source="llm", is_valid=True,
                ai_summaries=[{"text": t, "passages": [{"quote": p} for p in ps]} for t, ps in summaries])
    rule = Rule(id="r", rule_pack_id="p", rule_code="M3", rule_text="Leadership", rule_type="judgement", check_method="llm", params={"section": "merit"})
    return verify_findings([f], [rule], SOURCE, **kw)[0]


def test_every_bullet_links_to_a_passage_code_found(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    detail = queue_service.application_detail(store, officer, aid, settings)
    shown = 0
    for f in (x for x in detail["findings"] if x["section"] == "merit"):
        for b in f["ai_summaries_restored"]:
            if not b["linked"]:
                continue
            ok = [p for p in b["passages"] if p["verified"] and p["span"]]
            assert ok, "a bullet shown as linked has a located passage"
            for p in ok:
                text = detail["source_texts"][p["span"]["source"]]["text"]
                assert text[p["span"]["start"]:p["span"]["end"]] == p["span"]["text"]
            shown += 1
    assert shown >= 8


def test_academic_text_gives_several_bullets_and_supporting_evidence_covers_letters_and_answers(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings, "N01")
    detail = queue_service.application_detail(store, officer, aid, settings)
    by = {f["rule_code"]: f for f in detail["findings"]}
    assert by["M2"]["ai_summaries_restored"], "supporting evidence has summaries (not only the referee check)"
    assert by["M5"]["ai_summaries_restored"], "the short answer has summaries"
    assert len(by["M2"]["ai_summaries_restored"]) <= 6 and len(by["M5"]["ai_summaries_restored"]) <= 6


def test_an_unlinked_bullet_is_recorded_as_unlinked_and_never_invalidates_the_finding():
    f = verified([("Leads a club.", ["I lead the first-aid club of nine students."]), ("Invented.", ["a passage that is not in the text at all"]), ("No source.", [])])
    assert [b["linked"] for b in f.ai_summaries] == [True, False, False]
    assert f.is_valid is True


def test_a_passage_is_attributed_to_the_document_it_is_in_by_code_not_by_the_ai():
    f = verified([("Helped patients.", ["She helped every patient who came to the clinic"]),
                  ("Leads a club.", ["I lead the first-aid club of nine students."])], extra_sources={"document:letter1": LETTER})
    first, second = f.ai_summaries
    assert first["passages"][0]["source"] == "document:letter1" and first["linked"]
    assert "source" not in second["passages"][0] and second["linked"]       # found in the form: no source tag needed


def test_the_summary_schema_has_no_strength_or_quality_field():
    from app.pipeline.prompts import SummaryOut

    assert set(SummaryOut.model_fields) == {"summary", "passages"}


# ------------------------------------------------------------------ linked applications


@pytest.fixture(scope="module")
def pool():
    pytest.importorskip("presidio_analyzer")
    from tests.consistency.conftest import make_pool, stub

    store, ids, actor, settings = make_pool()
    for code in ("F07", "F08", "F09", "DD"):
        run_assessment(store, actor, ids[code], stub(), settings)
    return store, ids, actor, settings


def test_linked_applications_show_hashed_references_only(pool):
    store, ids, actor, settings = pool
    detail = queue_service.application_detail(store, actor, ids["F07"], settings)
    rows = detail["linked_applications"]
    assert rows and {r["strength"] for r in rows} <= {"Exact match", "Similar"}
    # Ids and hashed references are hex, which can contain digits such as "5550" by chance, so scan the rest.
    blob = json.dumps([{**r, "application_id": "", "shared": [{**i, "ref": ""} for i in r["shared"]]} for r in rows])
    for personal in ("example.org", "example.com", "0491", "5550", "Jacaranda", "Ashcombe", "@"):
        assert personal not in blob
    refs = [s["ref"] for r in rows for s in r["shared"]]
    assert any(ref.startswith("ref ") and len(ref) == 12 for ref in refs)        # "ref " + 8 hex of the keyed hash
    assert set(rows[0]) == {"application_id", "reference", "shared", "strength", "can_open"}     # no name, email or other personal field
    assert all(r["can_open"] for r in rows)


def test_there_are_no_links_for_an_application_that_shares_nothing(pool):
    store, ids, actor, settings = pool
    assert queue_service.application_detail(store, actor, ids["DD"], settings)["linked_applications"] == []


def test_open_is_offered_only_where_the_officer_has_access(pool):
    store, ids, actor, settings = pool
    other_org = Actor(user_id=actor.user_id, role="officer", organisation_id="00000000-0000-0000-0000-00000000dead")
    app = store.select("applications", eq={"id": ids["F07"]})[0]
    rows = linked.for_application(store, other_org, app, queue_service.reference)
    assert rows and not any(r["can_open"] for r in rows)


# ------------------------------------------------------------------ consistency of information


def test_the_overview_lists_every_comparison_including_the_ones_that_agree(pool):
    store, ids, actor, settings = pool
    rows = queue_service.application_detail(store, actor, ids["DD"], settings)["consistency"]["overview"]
    results = {r["compared"]: r["result"] for r in rows}
    assert results["Name on the form vs name on the CoE"] == "Differs"
    assert results["Course start date vs CoE start date"] == "Differs"
    assert results["Name on the form vs name on the passport"] == "Consistent"
    assert results["Arrival date on the form vs arrival date on the booking"] == "Cannot compare"     # the booking slot holds a letter of offer
    assert {r["result"] for r in rows} == {"Consistent", "Differs", "Cannot compare"}
    assert [r["result"] for r in rows] == sorted((r["result"] for r in rows), key=["Differs", "Cannot compare", "Consistent"].index)


def test_a_clean_application_is_consistent_everywhere(pool):
    store, ids, actor, settings = pool
    from app.services import consistency

    rows = queue_service.application_detail(store, actor, ids["DD"], settings)["consistency"]["overview"]
    assert all(set(r) >= {"check", "compared", "result", "decision", "left", "right"} for r in rows)


def test_both_values_come_with_their_source_and_an_exact_position(pool):
    store, ids, actor, settings = pool
    detail = queue_service.application_detail(store, actor, ids["DD"], settings)
    row = next(r for r in detail["consistency"]["overview"] if r["compared"] == "Course start date vs CoE start date")
    for side in (row["left"], row["right"]):
        text = detail["source_texts"][side["source"]]["text"]
        assert side["start"] is not None and text[side["start"]:side["end"]] == side["value"]
    assert row["left"]["source"] == "application_text" and row["right"]["source"].startswith("document:")


def test_overview_wording_is_neutral_and_links_to_other_applications_are_not_in_it(pool):
    store, ids, actor, settings = pool
    from app.pipeline.consistency.models import FORBIDDEN

    rows = queue_service.application_detail(store, actor, ids["F07"], settings)["consistency"]["overview"]
    assert not FORBIDDEN.search(json.dumps(rows))
    assert not any("application" in r["compared"].lower() and "other" in r["compared"].lower() for r in rows)


def test_dismissing_a_flag_still_needs_a_reason_and_never_blocks_sign_off(pool):
    store, ids, actor, settings = pool
    from app.services import consistency

    flag = next(f for f in store.select("consistency_flags", eq={"application_id": ids["DD"]}) if f["status"] == "open" and f["verification"] == "verified")
    with pytest.raises(ValidationFailed):
        consistency.review_flag(store, actor, flag["id"], "dismiss", "  ", settings)
    row = consistency.review_flag(store, actor, flag["id"], "dismiss", "Provider agreed the later date", settings)
    assert row["status"] == "dismissed"
    shown = {r["decision"] for r in queue_service.application_detail(store, actor, ids["DD"], settings)["consistency"]["overview"]}
    assert "Dismissed" in shown
