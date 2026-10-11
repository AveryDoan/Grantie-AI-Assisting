"""Step 7: redaction endpoints, RLS-equivalent access checks, and the wiring
into assessment (synthetic data only)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.ratelimit import limiter  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.llm.stub import OfflineStubProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from redaction.detector import Detector  # noqa: E402
from seed import data  # noqa: E402
from tests.conftest import ScriptedProvider, app_id  # noqa: E402
from tests.test_api import token  # noqa: E402

N01 = app_id("N01")
FIELDS = next(c for c in data.CASES if c["code"] == "N01")["application_text"]["fields"]
PERSONAL = ["Linh", "Tran", FIELDS["email"], FIELDS["phone"], FIELDS["date_of_birth"], "Fictional Lane", "Hoa Pham", "Minh Le"]


class CountingStub(ScriptedProvider):
    def __init__(self) -> None:
        stub = OfflineStubProvider()
        super().__init__(lambda p, s: stub.generate(p, s, temperature=0, timeout=1))


@pytest.fixture
def env(store, settings, officer, other_officer, applicant_actor):
    limiter.reset()
    provider = CountingStub()
    llm = LLMClient(provider, temperature=0.0)
    client = TestClient(create_app(store=store, settings=settings, llm_factory=lambda s, c: llm))
    return {"client": client, "store": store, "provider": provider, "settings": settings,
            "officer": token(settings, officer.user_id), "other": token(settings, other_officer.user_id),
            "applicant": token(settings, applicant_actor.user_id)}


def no_personal_values(blob: object) -> None:
    text = json.dumps(blob, default=str)
    for v in PERSONAL:
        assert v not in text, v


# ---------------------------------------------------------------- POST /redact


def test_redact_runs_stores_and_is_idempotent(env):
    c, h = env["client"], env["officer"]
    first = c.post(f"/applications/{N01}/redact", headers=h, json={})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["reused"] is False and body["report"]["status"] == "ok" and body["report"]["counts"]["PERSON"] >= 1
    no_personal_values(body)
    second = c.post(f"/applications/{N01}/redact", headers=h, json={}).json()
    assert second["reused"] is True and second["run_id"] == body["run_id"]

    app = env["store"].select("applications", eq={"id": N01})[0]
    assert app["ai_status"] == "ready" and app["location_class"] == "outside_australia"
    assert "[PERSON_1]" in app["redacted_text"] and "Linh" not in app["redacted_text"]
    rows = env["store"].select("redaction_token_maps", eq={"application_id": N01})
    assert rows and all(r["original_value_encrypted"].startswith("v1:") for r in rows)
    no_personal_values(rows)  # originals only ever stored encrypted
    docs = env["store"].select("documents", eq={"application_id": N01})
    assert all(d["extraction_status"] == "ok" for d in docs)
    assert all("Minh Le" not in (d["redacted_text"] or "") for d in docs)


def test_redaction_access_rules(env):
    c = env["client"]
    assert c.post(f"/applications/{N01}/redact", headers=env["applicant"], json={}).status_code == 403
    assert c.post(f"/applications/{N01}/redact", headers=env["other"], json={}).status_code == 404
    assert c.get(f"/applications/{N01}/redaction-report", headers=env["applicant"]).status_code == 403
    assert c.get(f"/applications/{N01}/original-view", headers=env["applicant"]).status_code == 403
    assert c.get(f"/applications/{N01}/original-view", headers=env["other"]).status_code == 404


# ---------------------------------------------------------------- report and original view


def test_report_has_no_values(env):
    c, h = env["client"], env["officer"]
    c.post(f"/applications/{N01}/redact", headers=h, json={})
    report = c.get(f"/applications/{N01}/redaction-report", headers=h).json()
    assert report["ai_status"] == "ready" and report["run"]["counts"]
    assert all("file_name" not in d for d in report["documents"])  # file names often contain names
    no_personal_values(report)


def test_original_view_restores_exactly_and_is_audited(env):
    c, h = env["client"], env["officer"]
    c.post(f"/applications/{N01}/redact", headers=h, json={})
    view = c.get(f"/applications/{N01}/original-view", headers=h).json()
    assert view["matches_stored_original"] is True
    assert "applicant_name: Linh Tran" in view["application_text"]
    assert any("Referee name: Mr Minh Le" in d["text"] for d in view["documents"])
    audit = [a for a in env["store"].select("audit_log") if a["action"] == "redaction.original_view"]
    assert len(audit) == 1 and audit[0]["actor_role"] == "officer"


def test_original_view_before_redaction_is_refused(env):
    assert env["client"].get(f"/applications/{N01}/original-view", headers=env["officer"]).status_code == 409


# ---------------------------------------------------------------- assessment wiring


def test_assessment_uses_redaction_and_officer_sees_real_words(env):
    c, h = env["client"], env["officer"]
    r = c.post(f"/applications/{N01}/assess", headers=h, json={})
    assert r.status_code == 200, r.text
    prompts = [p.user for p in env["provider"].calls]
    assert prompts and all("Linh" not in p and FIELDS["email"] not in p for p in prompts)  # the LLM saw tokens only
    detail = c.get(f"/applications/{N01}", headers=h).json()
    s8 = next(f for f in detail["findings"] if f["rule_code"] == "S8")
    assert s8["evidence_quote_restored"] and "[" not in s8["evidence_quote_restored"]
    m2 = next(f for f in detail["findings"] if f["rule_code"] == "M2")
    assert any("Hoa Pham" in q["quote"] for q in m2["supporting_quotes_restored"])  # officer sees the real referee
    stored_m2 = env["store"].select("findings", eq={"id": m2["id"]})[0]
    assert "Hoa Pham" not in json.dumps(stored_m2)  # ...but findings store tokens only


def test_planted_leak_stops_assessment_before_any_llm_call(env, monkeypatch):
    original = Detector.detect

    def blind(self, text, known=None, kind="free_text"):
        return [] if "MARKER" in text else original(self, text, known, kind)

    store = env["store"]
    app = store.select("applications", eq={"id": N01})[0]
    answers = dict(app["application_text"]["answers"])
    answers["community_engagement"] = "MARKER email me at planted.leak@example.invalid"
    store.update("applications", {"application_text": {**app["application_text"], "answers": answers}}, eq={"id": N01})
    monkeypatch.setattr(Detector, "detect", blind)

    r = env["client"].post(f"/applications/{N01}/assess", headers=env["officer"], json={})
    assert r.status_code == 409 and r.json()["error"] == "redaction_blocked"
    assert env["provider"].calls == []  # fail closed
    assert store.select("applications", eq={"id": N01})[0]["ai_status"] == "blocked_redaction_leak"
    assert any(a["action"] == "assessment.refused" for a in store.select("audit_log"))
    no_personal_values(store.select("audit_log"))
    assert "planted.leak" not in json.dumps(store.select("audit_log")) + r.text


def test_missing_redaction_key_refuses_everything(env, store, officer):
    s = env["settings"].model_copy(update={"redaction_key": type(env["settings"].redaction_key)("")})
    provider = CountingStub()
    client = TestClient(create_app(store=store, settings=s, llm_factory=lambda st, c: LLMClient(provider, temperature=0.0)))
    h = token(s, officer.user_id)
    assert client.post(f"/applications/{N01}/redact", headers=h, json={}).json()["error"] == "redaction_not_configured"
    assert client.post(f"/applications/{N01}/assess", headers=h, json={}).status_code == 409
    assert provider.calls == []


# ---------------------------------------------------------------- 11. audit entries never contain values


def test_audit_log_has_no_personal_values_after_full_flow(env):
    c, h = env["client"], env["officer"]
    c.post(f"/applications/{N01}/redact", headers=h, json={})
    c.get(f"/applications/{N01}/redaction-report", headers=h)
    c.get(f"/applications/{N01}/original-view", headers=h)
    c.post(f"/applications/{N01}/assess", headers=h, json={})
    entries = env["store"].select("audit_log")
    actions = {a["action"] for a in entries}
    assert {"redaction.run", "redaction.report_viewed", "redaction.original_view", "assessment.completed"} <= actions
    no_personal_values(entries)
