"""Flags are for officers to check: confirm or dismiss, audited, never a rule result and never a block."""

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from app.pipeline.orchestrator import run_assessment
from app.services import consistency as service
from app.services import queue, review
from app.services.access import Actor
from app.services.errors import Forbidden, NotFound, ValidationFailed
from app.store.base import StoreError
from seed import data
from tests.consistency.conftest import make_pool, make_settings, stub
from tests.test_api import token
from tests.test_workflow import decide_all


def assessed(code="F01", **kw):
    store, ids, actor, settings = make_pool()
    run_assessment(store, actor, ids[code], stub(), settings, **kw)
    return store, ids, actor, settings


def the_flag(store, ids, code="F01"):
    return store.select("consistency_flags", eq={"application_id": ids[code]})[0]


def test_a_dismissal_needs_a_note_and_both_decisions_are_audited():
    store, ids, actor, settings = assessed()
    f = the_flag(store, ids)
    assert f["status"] == "open"
    for note in (None, "", "  ", "x"):
        with pytest.raises(ValidationFailed, match="note"):
            service.review_flag(store, actor, f["id"], "dismiss", note, settings)
    assert the_flag(store, ids)["status"] == "open"
    done = service.review_flag(store, actor, f["id"], "dismiss", "The provider agreed a late start in writing.", settings)
    assert done["status"] == "dismissed" and done["note"].startswith("The provider") and done["reviewed_by"] == actor.user_id
    row = store.select("audit_log", eq={"action": "consistency.flag_dismissed"})[0]
    assert row["reason"].startswith("The provider") and row["actor_role"] == "officer" and row["application_id"] == ids["F01"]
    assert row["details"] == {"flag_id": f["id"], "check_id": "cross_document.arrival_vs_start", "strength": "strong", "type": "cross_document"}
    service.review_flag(store, actor, f["id"], "confirm", None, settings)           # a confirmation needs no note
    assert the_flag(store, ids)["status"] == "confirmed"
    assert store.select("audit_log", eq={"action": "consistency.flag_confirmed"})


def test_only_the_organisations_officers_can_decide_a_flag():
    store, ids, actor, settings = assessed()
    f = the_flag(store, ids)
    org = store.insert("organisations", {"name": "Org B"})[0]
    other = Actor(user_id="00000000-0000-0000-0000-0000000000b1", role="officer", organisation_id=org["id"])
    with pytest.raises(NotFound):
        service.review_flag(store, other, f["id"], "confirm", None, settings)
    with pytest.raises(Forbidden):
        service.review_flag(store, Actor(user_id="x", role="applicant"), f["id"], "confirm", None, settings)
    with pytest.raises(ValidationFailed):
        service.review_flag(store, actor, f["id"], "approve", "ok", settings)
    with pytest.raises(NotFound):
        service.review_flag(store, actor, "00000000-0000-0000-0000-00000000dead", "confirm", None, settings)


def test_the_api_requires_a_note_and_hides_flags_from_applicants():
    limiter.reset()
    store, ids, actor, settings = assessed()
    c = TestClient(create_app(store=store, settings=settings))
    h = token(settings, actor.user_id)
    f = the_flag(store, ids)
    assert c.post(f"/consistency-flags/{f['id']}/review", headers=h, json={"action": "dismiss"}).status_code == 422
    assert c.post(f"/consistency-flags/{f['id']}/review", headers=h, json={"action": "dismiss", "note": "Checked with the provider."}).status_code == 200
    store.insert("profiles", {"id": "00000000-0000-0000-0000-0000000000c9", "role": "applicant"})
    ha = token(settings, "00000000-0000-0000-0000-0000000000c9")
    assert c.post(f"/consistency-flags/{f['id']}/review", headers=ha, json={"action": "confirm"}).status_code == 403
    assert c.get("/pool/linked-applications", headers=ha).status_code == 403
    detail = c.get(f"/applications/{ids['F01']}", headers=h).json()
    assert detail["consistency"]["enabled"] and detail["consistency"]["flags"][0]["status"] == "dismissed"


def test_flags_never_change_a_rule_result():
    def statuses(layer: bool):
        store, ids, actor, settings = make_pool()
        settings = settings.model_copy(update={"consistency_layer": layer})
        run = run_assessment(store, actor, ids["F04"], stub(), settings)
        rules = {r["id"]: r["rule_code"] for r in store.select("rules")}
        return {rules[f["rule_id"]]: (f["ai_status"], f["is_valid"], f["quote_verified"]) for f in store.select("findings", eq={"run_id": run["id"]})}, store, ids

    with_layer, store_on, ids = statuses(True)
    without, store_off, ids_off = statuses(False)
    assert with_layer == without and len(with_layer) == 28
    assert store_on.select("consistency_flags", eq={"application_id": ids["F04"]})
    assert store_off.select("consistency_flags") == []


def test_open_strong_flags_do_not_block_sign_off():
    store, ids, actor, settings = make_pool()
    run = run_assessment(store, actor, ids["F01"], stub(), settings)
    findings = {store.select("rules", eq={"id": f["rule_id"]})[0]["rule_code"]: f for f in store.select("findings", eq={"run_id": run["id"]})}
    decide_all(store, actor, findings)
    assert [f for f in store.select("consistency_flags", eq={"application_id": ids["F01"]}) if f["status"] == "open" and f["strength"] == "strong"]
    review.sign_off(store, actor, ids["F01"], statement_acknowledged=True)
    assert store.select("applications", eq={"id": ids["F01"]})[0]["status"] == "signed_off"


# ---------------------------------------------------------------- the queue shows a count, nothing else


def test_the_queue_counts_open_flags_only_when_one_is_strong_and_never_sorts_by_them():
    store, ids, actor, settings = make_pool()
    for code in ("F01", "F06", "F10", "F02"):
        run_assessment(store, actor, ids[code], stub(), settings)
    rows = {r["id"]: r for r in queue.list_queue(store, actor, settings)}
    assert rows[ids["F01"]]["flags_to_check"] == 1
    assert rows[ids["F02"]]["flags_to_check"] == 3
    assert rows[ids["F06"]]["flags_to_check"] == 0 and rows[ids["F10"]]["flags_to_check"] == 0    # weak signals never highlight an application
    assert store.select("consistency_flags", eq={"application_id": ids["F06"]})                  # ...but they are on its review screen
    assert "flags_to_check" not in rows[ids["F01"]]["attention"] and not any("consistency" in k for k in rows[ids["F01"]]["attention"])
    open_items = {r["id"]: r["open_items"] for r in rows.values()}
    before = [r["id"] for r in queue.list_queue(store, actor, settings)]
    for f in store.select("consistency_flags", eq={"application_id": ids["F02"]}):
        service.review_flag(store, actor, f["id"], "dismiss", "Reviewed and explained by the applicant.", settings)
    after = queue.list_queue(store, actor, settings)
    assert {r["id"]: r for r in after}[ids["F02"]]["flags_to_check"] == 0
    assert [r["id"] for r in after] == before                                                    # the order does not depend on flags
    assert {r["id"]: r["open_items"] for r in after} == open_items                               # and neither do the open work items


def test_the_detail_view_restores_the_applicants_own_words():
    store, ids, actor, settings = make_pool()
    run_assessment(store, actor, ids["F03"], stub(), settings)
    flags = queue.application_detail(store, actor, ids["F03"], settings)["consistency"]["flags"]
    quotes = [e for f in flags for e in f["evidence"] if e["kind"] == "quote"]
    assert quotes and all("[PERSON_" not in e["restored"] and "[REFEREE_" not in e["restored"] for e in quotes)
    assert any("Nguyet" in e["restored"] for e in quotes)           # redacted when stored, restored for the officer


def test_a_reassessment_keeps_decisions_and_removes_only_open_flags_no_longer_raised():
    store, ids, actor, settings = make_pool()
    run_assessment(store, actor, ids["F02"], stub(), settings)
    flags = {f["check_id"]: f for f in store.select("consistency_flags", eq={"application_id": ids["F02"]})}
    service.review_flag(store, actor, flags["cross_document.coe_length"]["id"], "dismiss", "The provider confirmed the dates are right.", settings)
    # Fix one thing in the documents: the 2022 letter is replaced by a 2026 one.
    for d in store.select("documents", eq={"application_id": ids["F02"]}):
        key = ("application-documents", d["storage_path"])
        if b"18 May 2022" in store.files[key]:
            store.files[key] = store.files[key].replace(b"18 May 2022", b"18 May 2026")   # same length: the PDF stays valid
    run_assessment(store, actor, ids["F02"], stub(), settings, force=True)
    now = {f["check_id"]: f for f in store.select("consistency_flags", eq={"application_id": ids["F02"]})}
    assert "cross_document.referee_date_window" not in now                       # open and no longer raised: removed
    assert now["cross_document.coe_length"]["status"] == "dismissed" and now["cross_document.coe_length"]["note"].startswith("The provider")
    assert now["cross_document.coe_length"]["id"] == flags["cross_document.coe_length"]["id"]     # the same row, not a copy


# ---------------------------------------------------------------- off, or not yet migrated


def test_with_the_layer_off_nothing_is_run_stored_or_shown(store, settings, officer):
    run_assessment(store, officer, data.sid("application:N01"), stub(), settings)
    assert store.select("consistency_flags") == [] and store.select("identifier_hashes") == []
    assert queue.application_detail(store, officer, data.sid("application:N01"), settings)["consistency"] == {"enabled": False, "flags": [], "trace": {}}
    assert all(r["flags_to_check"] == 0 for r in queue.list_queue(store, officer, settings))
    assert service.linked_groups(store, officer, settings) == []


def test_if_the_tables_do_not_exist_yet_the_app_carries_on():
    store, ids, actor, settings = assessed("F01")

    class NoTable:
        def __getattr__(self, name):
            return getattr(store, name)

        def select(self, table, **kw):
            if table in ("consistency_flags", "identifier_hashes", "document_fingerprints"):
                raise StoreError("relation does not exist")
            return store.select(table, **kw)

    s = NoTable()
    assert all(r["flags_to_check"] == 0 for r in queue.list_queue(s, actor, settings))
    view = queue.application_detail(s, actor, ids["F01"], settings)["consistency"]
    assert view["enabled"] is False and view["flags"] == [] and view["unavailable"] is True
    assert service.linked_groups(s, actor, settings) == []
