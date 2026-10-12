"""AI-use check: a weak signal from a local detector. It runs only on request, only when the fairness gate switched it on,
only on configured fields over the word minimum, reads redacted text, is logged, and NEVER reaches a rule status, a merit
mark, a letter, the panel summary or the outcome. Synthetic data only; the detector is replaced by a fake in these tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline import ai_use as detector
from app.services import ai_use, intake, letters, outcome, queue as queue_service, review, steps
from app.services.errors import Conflict, ValidationFailed
from app.store.base import one
from seed import data
from tests.test_merit_review import assessed, mark
from tests.test_substeps import decide_every_rule

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = {"weak": 0.9, "stronger": 0.98}
LONG = " ".join(["I started a coding club for girls in two rural schools and we meet every Saturday."] * 12)


def count_words(text):
    return len(text.split())


def scorer(value):
    return lambda wins: [value] * len(wins)


def switch_on(store, aid, enabled=True):
    app = one(store.select("applications", eq={"id": aid}, limit=1))
    pack = one(store.select("rule_packs", eq={"id": app["rule_pack_id"]}, limit=1))
    cfg = {**pack["letter_config"], "ai_use_policy": data.AI_USE_POLICY,
           "ai_check_fields": {"fields": ["academic_achievements", "leadership", "community_engagement"], "min_words": 80},
           "ai_check": {"enabled": enabled}}
    store.update("rule_packs", {"letter_config": cfg}, eq={"id": pack["id"]})


def give_answers(store, aid, **answers):
    """Put long synthetic answers in the application's redacted text (the check reads redacted text only)."""
    app = one(store.select("applications", eq={"id": aid}, limit=1))
    extra = "\n".join(f"{k}: {v}" for k, v in answers.items())
    store.update("applications", {"redacted_text": (app.get("redacted_text") or "") + "\n" + extra}, eq={"id": aid})


# ---------------------------------------------------------------- the detector wrapper (no model needed)

def test_redaction_tokens_become_neutral_words():
    assert detector.neutral("I met [PERSON_2] in [LOCATION_1] and wrote to [EMAIL_1].") == "I met the person in the place and wrote to the email."
    assert "[" not in detector.neutral("[ORG_12] and [SOMETHING_ELSE_3]")


def test_windows_follow_sentences_and_respect_the_token_limit():
    text = " ".join(f"Sentence number {i} is here." for i in range(50))
    wins = detector.windows(text, count_words, limit=40)
    assert all(count_words(w) <= 40 for w in wins) and " ".join(wins) == text


def test_results_are_four_neutral_bands():
    assert detector.band(None, THRESHOLDS) == "Too short to check"
    assert detector.band(0.5, THRESHOLDS) == "No signal"
    assert detector.band(0.95, THRESHOLDS) == "Weak signal"
    assert detector.band(0.99, THRESHOLDS) == "Stronger signal"


def test_short_fields_are_not_checked():
    out = detector.check_fields({"a": "Too short.", "b": LONG}, scorer=scorer(0.5), count_tokens=count_words, thresholds=THRESHOLDS, min_words=80)
    assert [(r.field, r.result) for r in out] == [("a", "Too short to check"), ("b", "No signal")]
    assert out[0].scores == []


# ---------------------------------------------------------------- the service

def test_it_is_off_unless_the_pack_has_a_policy_and_the_gate_switched_it_on(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    switch_on(store, aid, enabled=False)
    with pytest.raises(Conflict, match="not switched on"):
        ai_use.run_check(store, officer, aid, scorer=scorer(0.99), count_tokens=count_words, info={"model": "fake", "revision": "r1"})
    assert store.select("ai_use_checks") == []


def test_a_run_checks_only_configured_fields_keeps_the_trace_and_is_logged(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    switch_on(store, aid)
    give_answers(store, aid, academic_achievements=LONG, leadership="Short answer.", nt_contribution=LONG)
    seen = []
    out = ai_use.run_check(store, officer, aid, scorer=lambda w: seen.extend(w) or [0.99] * len(w), count_tokens=count_words,
                           info={"model": "fake-detector", "revision": "r1"})
    results = {f["field"]: f["result"] for f in out["fields"]}
    assert set(results) == {"academic_achievements", "leadership", "community_engagement"}     # nt_contribution is not configured
    assert results["leadership"] == "Too short to check" and results["academic_achievements"] == "Stronger signal"
    assert all("[" not in w for w in seen), "the detector never sees a redaction token"
    trace = store.select("ai_use_checks")[0]
    assert trace["model"] == "fake-detector" and trace["revision"] == "r1" and trace["thresholds"] and trace["fields"][0]["scores"]
    logged = [a for a in store.select("audit_log") if a["action"] == "ai_use_check.run"]
    assert logged and "coding club" not in json.dumps(logged[0]["details"]) and "mean" not in json.dumps(logged[0]["details"])
    assert not [f for f in out["fields"] if "mean" in f or "scores" in f], "no score or percentage reaches the screen"


def test_officer_actions_and_a_neutral_request(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    switch_on(store, aid)
    give_answers(store, aid, academic_achievements=LONG)
    ai_use.run_check(store, officer, aid, scorer=scorer(0.99), count_tokens=count_words, info={"model": "fake", "revision": "r1"})
    app = one(store.select("applications", eq={"id": aid}, limit=1))
    assert ai_use.open_count(store, app)["open"] == 1
    with pytest.raises(ValidationFailed):
        ai_use.record_action(store, officer, aid, "academic_achievements", "note", "  ")
    out = ai_use.record_action(store, officer, aid, "academic_achievements", "ask")
    draft = out["draft"]["message_text"].lower()
    for word in ("ai", "detector", "machine", "generated", "suspic", "fraud", "fake", "cheat", "signal", "score"):
        assert word not in draft.split() and f" {word}" not in draft, word
    assert out["draft"]["status"] == "draft" and out["draft"]["sent_at"] is None, "nothing is sent without the officer"
    assert ai_use.open_count(store, app)["open"] == 0
    assert [a["action"] for a in store.select("audit_log") if a["action"].startswith("ai_use_check")] == ["ai_use_check.run", "ai_use_check.action"]


def test_the_check_never_reaches_status_merit_letters_summary_or_outcome(store, officer, stub_llm, settings):
    aid = assessed(store, officer, stub_llm, settings)
    switch_on(store, aid)
    give_answers(store, aid, academic_achievements=LONG, leadership=LONG, community_engagement=LONG)
    app = one(store.select("applications", eq={"id": aid}, limit=1))

    def snapshot():
        st = steps.state(store, one(store.select("applications", eq={"id": aid}, limit=1)), settings)
        detail = queue_service.application_detail(store, officer, aid, settings)
        return {
            "statuses": [(s["step"], s["status"]) for s in st["steps"]],
            "pending": review.undecided_rules(store, aid)[1],
            "blocking": [(s["key"], s["count"], s["status"]) for s in st["steps"][2]["substeps"] if s["blocking"]],
            "findings": [(f["rule_code"], f["ai_status"], f["merit_mark"]) for f in detail["findings"]],
            "merit": detail["merit_summaries"],
            "outcome": outcome.build(store, officer, aid, settings),
            "app_status": one(store.select("applications", eq={"id": aid}, limit=1))["status"],
        }

    before = snapshot()
    ai_use.run_check(store, officer, aid, scorer=scorer(0.999), count_tokens=count_words, info={"model": "fake", "revision": "r1"})
    ai_use.record_action(store, officer, aid, "leadership", "note", "Spoke about it with the team.")
    assert snapshot() == before, "the AI-use check changed something it must never touch"
    # a decline letter drafted afterwards says nothing about it
    ok = [f for f in store.select("findings") if f["application_id"] == aid and f["ai_status"] in ("Met", "Not met", "Unclear", "Needs evidence")]
    review.review_finding(store, officer, ok[0]["id"], action="override", final_status="Not met", reason="Not met on the documents")
    decide_every_rule(store, officer, aid)
    for code in ("M1", "M2", "M3", "M4", "M5"):
        mark(store, officer, aid, code)
    letter = letters.generate_letter(store, officer, aid, settings)
    assert not any(w in letter["body_text"].lower() for w in ("ai-use", "detector", "signal", "ai use", "machine"))


def test_no_status_letter_or_summary_module_reads_the_ai_use_check():
    for mod in ("app/services/outcome.py", "app/services/letters.py", "app/services/merit.py", "app/services/merit_summary.py",
                "app/services/review.py", "app/pipeline/merit_claims.py", "app/pipeline/evaluate.py", "app/pipeline/orchestrator.py"):
        src = (ROOT / mod).read_text(encoding="utf-8")
        assert "ai_use" not in src and "ai-use" not in src.lower(), mod
    # steps only takes the pill count through substeps, never into a status
    assert "ai_use" not in (ROOT / "app/services/steps.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the applicant's acknowledgement

def test_the_acknowledgement_is_stored_and_the_policy_is_offered(store, applicant_actor, officer, stub_llm, settings):
    progs = intake.programs(store)
    snt = next(p for p in progs if p["id"] == data.SNT_PROGRAM)
    assert snt["ai_use_policy"] == data.AI_USE_POLICY
    made = intake.create_draft(store, applicant_actor, grant_program_id=data.SNT_PROGRAM,
                               fields={"applicant_name": "Test Person", "ai_use_acknowledged": "Yes"}, answers={})
    row = one(store.select("applications", eq={"id": made["id"]}, limit=1))
    assert row["ai_use_acknowledged"] is True and row["ai_use_acknowledged_at"]
    first = row["ai_use_acknowledged_at"]
    intake.update_draft(store, applicant_actor, made["id"], fields={"applicant_name": "Test Person", "ai_use_acknowledged": "Yes"}, answers={})
    assert one(store.select("applications", eq={"id": made["id"]}, limit=1))["ai_use_acknowledged_at"] == first
    intake.update_draft(store, applicant_actor, made["id"], fields={"applicant_name": "Test Person"}, answers={})
    assert one(store.select("applications", eq={"id": made["id"]}, limit=1))["ai_use_acknowledged"] is False


# ---------------------------------------------------------------- the real model, when it is downloaded

def test_the_local_model_runs_offline_when_present():
    pytest.importorskip("transformers")
    try:
        score, count, info = detector.local_detector()
    except detector.DetectorUnavailable:
        pytest.skip("detector model not downloaded")
    out = detector.check_fields({"a": LONG, "b": "Short."}, scorer=score, count_tokens=count, thresholds=THRESHOLDS)
    assert out[1].result == "Too short to check" and out[0].result in detector.RESULTS and 0.0 <= out[0].mean <= 1.0
    assert info["revision"] == detector.load_config()["revision"]
