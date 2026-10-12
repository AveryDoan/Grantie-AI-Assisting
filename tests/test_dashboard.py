"""Officer Overview: counts only, from the same code as the queue and the step bar. Synthetic data only.

Brief tests 1 (counts agree), 2 (no ranking fields), 3 (merit privacy), 4 (suggested vs confirmed), 5 (AI-use off),
6 (no per-officer league), 10 (access) and 11 (empty state)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from app.services import dashboard, review, steps
from app.pipeline.orchestrator import run_assessment
from app.store.base import now_iso, one
from seed.data import SNT_PACK
from tests.conftest import app_id
from tests.test_api import keys_in, token
from tests.test_merit_review import mark
from tests.test_substeps import CODES, decide_every_rule

TILES = ("received", "need_action", "waiting_applicant", "ready_signoff", "signed_off", "not_started")
BANNED_KEYS = {"score", "rank", "ranking", "total_mark", "average_mark", "avg_mark", "mark", "marks", "top", "best",
               "competitiveness", "officer_id", "actor_id", "by_officer"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    dashboard.clear_cache()
    yield
    dashboard.clear_cache()


@pytest.fixture
def client(store, settings, stub_llm):
    limiter.reset()
    return TestClient(create_app(store=store, settings=settings, llm_factory=lambda s, c: stub_llm))


def _app(store, aid):
    return one(store.select("applications", eq={"id": aid}, limit=1))


def _all_met(store, officer, aid):
    """Every eligibility finding decided Met by the officer, every merit criterion marked."""
    _, pending = review.undecided_rules(store, aid)
    for f in store.select("findings", eq={"application_id": aid}):
        rule = one(store.select("rules", eq={"id": f["rule_id"]}, limit=1))
        if rule["rule_code"] in pending and (rule.get("params") or {}).get("section") != "merit":
            review.review_finding(store, officer, f["id"], action="override" if f["ai_status"] != "Met" else "confirm",
                                  final_status="Met", reason="Checked against the documents")
    for code in CODES:
        mark(store, officer, aid, code)


@pytest.fixture
def known(store, officer, stub_llm, settings):
    """A round with known numbers: all Met, one Not met, Needs evidence waiting on the applicant, signed off, one AI-suggested only."""
    ids = {k: app_id(c) for k, c in (("all_met", "N01"), ("not_met", "N02"), ("waiting", "N03"), ("signed", "N04"), ("suggested", "N05"))}
    for aid in ids.values():
        run_assessment(store, officer, aid, stub_llm, settings)
    _all_met(store, officer, ids["all_met"])
    # one Not met, confirmed by the officer; the rest decided and marked
    _all_met(store, officer, ids["not_met"])
    f = next(f for f in store.select("findings", eq={"application_id": ids["not_met"]})
             if (one(store.select("rules", eq={"id": f["rule_id"]}, limit=1)).get("params") or {}).get("section") != "merit")
    review.review_finding(store, officer, f["id"], action="override", final_status="Not met", reason="The document shows otherwise")
    # Needs evidence: the officer asked the applicant, who has not replied yet
    f = store.select("findings", eq={"application_id": ids["waiting"]})[0]
    review.review_finding(store, officer, f["id"], action="ask_applicant")
    store.update("applications", {"status": "awaiting_applicant"}, eq={"id": ids["waiting"]})
    # signed off
    _all_met(store, officer, ids["signed"])
    store.update("applications", {"status": "signed_off"}, eq={"id": ids["signed"]})
    store.insert("sign_offs", {"application_id": ids["signed"], "officer_id": officer.user_id, "signed_at": now_iso(), "statement_acknowledged": True})
    return ids


# ------------------------------------------------------------------ 1. counts agree

def test_tiles_equal_the_rows_their_queue_link_shows(client, settings, officer, known):
    h = token(settings, officer.user_id)
    d = client.get("/dashboard", headers=h).json()
    assert d["round"]["id"] == SNT_PACK
    for k in TILES:
        rows = client.get(f"/applications?filter={k}&rule_pack={SNT_PACK}", headers=h)
        assert rows.status_code == 200 and len(rows.json()) == d["tiles"][k], k
    for card in d["pipeline"]:
        if card["enabled"]:
            rows = client.get(f"/applications?filter=stage:{card['stage']}&rule_pack={SNT_PACK}", headers=h).json()
            assert len(rows) == card["count"], card["stage"]
            assert card["needs_you"] + card["waiting_applicant"] == (card["count"] if card["stage"] != "signed_off" else card["waiting_applicant"])
    # each application is in exactly one primary stage, so those cards add up to Received
    primary = [c["count"] for c in d["pipeline"] if not c["never_blocks"]]
    assert sum(primary) == d["tiles"]["received"]


def test_known_numbers_and_the_header_agree(store, officer, settings, known):
    d = dashboard.overview(store, officer, settings)
    t = d["tiles"]
    assert t["signed_off"] == 1 and t["waiting_applicant"] == 1
    assert t["ready_signoff"] == 2                   # all Met, and one Not met (a decline): both wait for sign-off
    assert t["received"] == 9 and t["need_action"] == t["received"] - t["signed_off"] - t["waiting_applicant"]
    assert d["findings"]["confirmed"]["not_met_apps"] == 1
    assert d["findings"]["confirmed"]["all_met_apps"] == 2      # all Met, and the signed-off one
    # the queue row, the overview and the application's own step bar give the same stage
    for row in dashboard.rows(store, officer, settings, rule_pack=SNT_PACK):
        app = _app(store, row["id"])
        st = steps.state(store, app, settings)
        assert row["stage"] == steps.stage_of(app, st)
        assert row["current_step"] == (4 if app["status"] == "signed_off" else st["current"])


def test_needs_attention_is_oldest_first_and_links_to_the_step(store, officer, settings, known):
    d = dashboard.overview(store, officer, settings)
    waits = [a["waiting_days"] for a in d["attention"]]
    assert waits == sorted(waits, reverse=True)
    assert all(a["deep_link"].startswith(f"#/applications/{a['application_id']}?step=") for a in d["attention"])
    assert len(d["attention"]) <= 8 and d["attention_total"] == d["tiles"]["need_action"]


# ------------------------------------------------------------------ 2 and 3. no ranking, merit privacy

def test_no_ranking_fields_and_no_mark_values(store, officer, settings, known):
    d = dashboard.overview(store, officer, settings)
    assert not (keys_in(d) & BANNED_KEYS)
    for m in d["merit_progress"]:
        assert set(m) == {"criterion", "title", "marked", "total"}
    # the marks exist (70 each), but no value from them appears anywhere
    assert store.select("merit_marks")
    assert "70" not in json.dumps(d["merit_progress"])
    marked = {m["criterion"]: m for m in d["merit_progress"]}
    assert marked["M1"]["marked"] == 3 and marked["M1"]["total"] == 5


# ------------------------------------------------------------------ 4. suggested vs confirmed

def test_an_undecided_ai_finding_counts_only_as_suggested(store, officer, stub_llm, settings):
    aid = app_id("N05")
    run_assessment(store, officer, aid, stub_llm, settings)
    d = dashboard.overview(store, officer, settings)
    f = d["findings"]
    assert f["suggested_pending"] > 0
    assert f["confirmed"] == {"not_met_apps": 0, "all_met_apps": 0}
    assert f["by_status"]["met"] == f["by_status"]["not_met"] == f["by_status"]["needs_evidence"] == 0
    ai_not_met = [x for x in store.select("findings", eq={"application_id": aid}) if x["ai_status"] == "Not met"]
    if ai_not_met:   # the AI said Not met; the officer has not decided: it is not a confirmed Not met
        dashboard.clear_cache()
        review.review_finding(store, officer, ai_not_met[0]["id"], action="confirm", reason="Read the document")
        f2 = dashboard.overview(store, officer, settings)["findings"]
        assert f2["by_status"]["not_met"] == 1 and f2["suggested_pending"] == f["suggested_pending"] - 1


# ------------------------------------------------------------------ 5. AI-use off

def test_ai_use_card_is_off_and_signal_hidden_when_the_gate_is_off(store, officer, settings, known):
    pack = one(store.select("rule_packs", eq={"id": SNT_PACK}, limit=1))
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "ai_check": {"enabled": False}}}, eq={"id": SNT_PACK})
    d = dashboard.overview(store, officer, settings)
    card = next(c for c in d["pipeline"] if c["stage"] == "ai_use")
    assert card["enabled"] is False and card["count"] == 0
    assert d["signals"]["ai_use_enabled"] is False and "ai_use_pending" not in d["signals"]
    dashboard.clear_cache()
    store.update("rule_packs", {"letter_config": {**pack["letter_config"], "ai_check": {"enabled": True}}}, eq={"id": SNT_PACK})
    d = dashboard.overview(store, officer, settings)
    assert next(c for c in d["pipeline"] if c["stage"] == "ai_use")["enabled"] is True
    assert "ai_use_pending" in d["signals"]


# ------------------------------------------------------------------ 6. no per-officer league; Me is my own work

def test_me_is_my_own_applications_and_nothing_is_keyed_by_officer(store, officer, settings, known):
    from tests.conftest import sid

    colleague_id = sid("user:colleague")
    store.insert("profiles", {"id": colleague_id, "role": "officer", "organisation_id": officer.organisation_id})
    d_all = dashboard.overview(store, officer, settings)
    me = dashboard.overview(store, officer, settings, officer="me")
    assert me["tiles"]["received"] == 5 < d_all["tiles"]["received"]    # the five I acted on
    text = json.dumps(d_all)
    assert officer.user_id not in text and colleague_id not in text


# ------------------------------------------------------------------ 10. access

def test_officer_from_another_office_sees_nothing(client, settings, other_officer, known):
    h = token(settings, other_officer.user_id)
    d = client.get("/dashboard", headers=h).json()
    assert d["tiles"]["received"] == 0 and d["attention"] == [] and d["round"] is None
    assert client.get("/applications?filter=received", headers=h).json() == []


def test_applicant_cannot_read_the_overview(client, settings, applicant_actor):
    assert client.get("/dashboard", headers=token(settings, applicant_actor.user_id)).status_code == 403


# ------------------------------------------------------------------ cache and writes

def test_reading_writes_nothing_and_is_cached(store, officer, settings, known):
    before = len(store.select("audit_log"))
    a = dashboard.overview(store, officer, settings)
    b = dashboard.overview(store, officer, settings)
    assert a is b                                    # served from the 30-second cache
    assert dashboard.overview(store, officer, settings, refresh=True) is not a
    assert len(store.select("audit_log")) == before


def test_unknown_filter_is_rejected(client, settings, officer):
    assert client.get("/applications?filter=best", headers=token(settings, officer.user_id)).status_code == 422
