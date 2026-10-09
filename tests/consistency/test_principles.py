"""The principles, as tests: signals not verdicts, no accusing words, no raw identifiers outside the code that hashes them."""

import json
import logging
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.logging_utils import configure_logging
from app.main import create_app
from app.pipeline.consistency import cross_document
from app.pipeline.orchestrator import run_assessment
from app.services import consistency as service
from tests.consistency.conftest import make_pool, stub
from tests.test_api import token

ROOT = Path(__file__).resolve().parents[2]
BANNED = re.compile(r"(?i)\b(fraud\w*|fake\w*|guilty|suspicious)\b")
RING = ("F07", "F08", "F09")


def test_no_output_string_contains_the_words_fraud_fake_guilty_or_suspicious(pool):
    limiter.reset()
    store, actor, settings = pool["store"], pool["actor"], pool["settings"]
    c = TestClient(create_app(store=store, settings=settings))
    h = token(settings, actor.user_id)
    outputs = [c.get("/applications", headers=h).text, c.get("/pool/linked-applications", headers=h).text, c.get("/audit-log", headers=h).text]
    outputs += [c.get(f"/applications/{i}", headers=h).text for i in pool["ids"].values()]
    outputs += [json.dumps(store.select(t), default=str) for t in ("consistency_flags", "identifier_hashes", "audit_log")]
    outputs += [json.dumps(r["consistency_trace"], default=str) for r in store.select("assessment_runs")]
    outputs += [json.dumps(service.linked_groups(store, actor, settings))]
    for text in outputs:
        assert not BANNED.search(text), BANNED.search(text).group(0)
    assert sum(len(json.loads(t) if t.startswith("[") else [1]) for t in outputs[:2]) > 10     # the scan covered real data


def test_the_words_never_appear_in_the_ui_code_or_the_consistency_modules():
    files = [*(ROOT / "frontend/src").rglob("*.ts*"), *(ROOT / "frontend/src").rglob("*.css"),
             *(ROOT / "app/pipeline/consistency").glob("*.py"), ROOT / "app/services/consistency.py"]
    assert len(files) > 20
    for f in files:
        m = BANNED.search(f.read_text(encoding="utf-8"))
        assert not m, f"{f.relative_to(ROOT)}: {m.group(0)}"


def test_no_output_has_a_score_rating_ranking_or_recommendation(pool):
    banned_keys = {"score", "risk", "rating", "rank", "ranking", "recommendation", "verdict", "probability", "likelihood", "confidence_score"}

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    from app.services import queue

    for aid in pool["ids"].values():
        detail = queue.application_detail(pool["store"], pool["actor"], aid, pool["settings"])
        assert not banned_keys & set(keys(detail["consistency"]))
    assert not banned_keys & set(keys(service.linked_groups(pool["store"], pool["actor"], pool["settings"])))
    assert not banned_keys & set(keys(queue.list_queue(pool["store"], pool["actor"], pool["settings"])))


RAW = ["dhruv.ashcombe@example.com", "d.crossley@riverbend-institute.example.org", "m.lindqvist@riverbend-institute.example.org",
       "s.osei@riverbend-institute.example.org", "riverbend-institute", "0491 570 313", "491570313", "5550 3344", "55503344", "(07) 5550",
       "16 Prayag Marg", "prayag marg", "Daniel Crossley", "Maya Lindqvist", "Samuel Osei", "Crossley", "Lindqvist", "Osei",
       "Yuki Pemberley", "Amara Locksley", "Dhruv Ashcombe"]


def test_raw_identifiers_never_reach_logs_audit_entries_or_stored_flags(caplog):
    configure_logging(logging.DEBUG)      # as the app does: third-party loggers that print document content stay quiet
    caplog.set_level(logging.DEBUG)
    store, ids, actor, settings = make_pool()
    for code in RING:
        run_assessment(store, actor, ids[code], stub(), settings)
    for f in store.select("consistency_flags"):
        service.review_flag(store, actor, f["id"], "dismiss", "Checked and explained.", settings)
    text = {
        "logs": "\n".join(r.getMessage() for r in caplog.records),
        "audit": json.dumps(store.select("audit_log"), default=str),
        "flags": json.dumps(store.select("consistency_flags"), default=str),
        "hashes": json.dumps(store.select("identifier_hashes") + store.select("document_fingerprints"), default=str),
        "traces": json.dumps([r["consistency_trace"] for r in store.select("assessment_runs")], default=str),
    }
    for where, blob in text.items():
        low = blob.lower()
        for raw in RAW:
            assert raw.lower() not in low, (where, raw)
    assert len(text["audit"]) > 100 and len(text["flags"]) > 100        # there was real data to look through


def test_a_failing_check_is_recorded_by_type_only_and_never_stops_the_assessment(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    store, ids, actor, settings = make_pool()

    def boom(ctx):
        raise ValueError("secret.person@example.com 0491 570 313")

    monkeypatch.setattr(cross_document, "run", boom)
    run = run_assessment(store, actor, ids["F01"], stub(), settings)
    assert run["status"] == "complete" and store.select("findings", eq={"run_id": run["id"]})
    trace = store.select("assessment_runs", eq={"id": run["id"]})[0]["consistency_trace"]
    assert trace["cross_document"]["error"] == "ValueError"
    assert "secret.person" not in json.dumps(trace) and "secret.person" not in caplog.text and "570 313" not in caplog.text
