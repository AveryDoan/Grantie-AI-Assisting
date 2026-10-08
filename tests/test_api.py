"""HTTP-level tests: auth, role checks, no scores/rankings, no bulk approve, rate limits."""

from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from seed import data
from tests.conftest import app_id

FORBIDDEN_KEYS = {
    "score", "eligibility_score", "overall_score", "overall_status", "recommendation", "recommended_decision",
    "rank", "ranking", "eligible", "is_eligible", "pass", "fail", "approve_recommended", "reject_recommended",
}


def token(settings, user_id: str) -> dict[str, str]:
    t = jwt.encode({"sub": user_id, "aud": "authenticated", "role": "authenticated"},
                   settings.supabase_jwt_secret.get_secret_value(), algorithm="HS256")
    return {"Authorization": f"Bearer {t}"}


def keys_in(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(keys_in(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(keys_in(v) for v in obj)) if obj else set()
    return set()


@pytest.fixture
def client(store, settings, stub_llm):
    limiter.reset()
    app = create_app(store=store, settings=settings, llm_factory=lambda s, c: stub_llm)
    return TestClient(app)


def test_requires_auth(client):
    assert client.get("/applications").status_code == 401
    assert client.get("/applications", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_full_officer_flow_has_no_scores(client, settings, officer, applicant_actor):
    h = token(settings, officer.user_id)
    responses = []
    q = client.get("/applications", headers=h)
    assert q.status_code == 200 and len(q.json()) == 15
    responses.append(q.json())

    r = client.post(f"/applications/{app_id('S04')}/assess", headers=h, json={})
    assert r.status_code == 200, r.text
    responses.append(r.json())

    detail = client.get(f"/applications/{app_id('S04')}", headers=h).json()
    responses.append(detail)
    findings = detail["findings"]
    assert len(findings) == 7 and all("latest_review" in f for f in findings)

    blocked = client.post(f"/applications/{app_id('S04')}/signoff", headers=h, json={"statement_acknowledged": True})
    assert blocked.status_code == 409 and blocked.json()["error"] == "signoff_blocked"

    bad = client.post(f"/findings/{findings[0]['id']}/review", headers=h, json={"action": "override", "final_status": "Not met"})
    assert bad.status_code == 422

    for f in findings:
        if f["ai_status"] == "Evidence only":
            body = {"action": "override", "final_status": "Met", "reason": "Officer judgement on the evidence"}
        elif f["ai_status"] == "Not met":
            body = {"action": "confirm", "reason": "Visa expiry is before the closing date"}
        else:
            body = {"action": "confirm"}
        resp = client.post(f"/findings/{f['id']}/review", headers=h, json=body)
        assert resp.status_code == 200, resp.text
        responses.append(resp.json())

    letter = client.post(f"/applications/{app_id('S04')}/letter", headers=h)
    assert letter.status_code == 200, letter.text
    responses.append(letter.json())
    signed = client.post(f"/applications/{app_id('S04')}/signoff", headers=h, json={"statement_acknowledged": True})
    assert signed.status_code == 200
    approved = client.patch(f"/letters/{letter.json()['id']}", headers=h, json={"approve": True})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    responses.append(approved.json())

    audit = client.get("/audit-log", headers=h, params={"application_id": app_id("S04")})
    assert audit.status_code == 200 and len(audit.json()) >= 10
    responses.append(audit.json())
    csv = client.get("/audit-log", headers=h, params={"format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv") and "officer_decision" in csv.text

    for body in responses:
        assert not (keys_in(body) & FORBIDDEN_KEYS), keys_in(body) & FORBIDDEN_KEYS


def test_applicant_cannot_use_officer_endpoints(client, settings, applicant_actor):
    h = token(settings, applicant_actor.user_id)
    assert client.get("/applications", headers=h).status_code == 403
    assert client.post(f"/applications/{app_id('S01')}/assess", headers=h, json={}).status_code == 403
    assert client.get("/audit-log", headers=h).status_code == 403
    own = client.get(f"/applications/{app_id('S01')}", headers=h)
    assert own.status_code == 200 and "findings" not in own.json()
    assert client.get(f"/applications/{app_id('S02')}", headers=h).status_code == 404


def test_request_manual_then_assess_refused(client, settings, officer, applicant_actor):
    r = client.post(f"/applications/{app_id('S01')}/request-manual", headers=token(settings, applicant_actor.user_id))
    assert r.status_code == 200 and r.json()["manual_assessment_requested"] is True
    a = client.post(f"/applications/{app_id('S01')}/assess", headers=token(settings, officer.user_id), json={})
    assert a.status_code == 409 and a.json()["error"] == "manual_assessment_requested"


def test_precheck_endpoint_always_offers_options(client, settings, applicant_actor):
    r = client.post("/applications/precheck", headers=token(settings, applicant_actor.user_id),
                    json={"grant_program_id": data.CBF_PROGRAM, "fields": {}, "documents": []})
    assert r.status_code == 200
    body = r.json()
    assert body["options"]["submit_anyway"]["available"] and body["options"]["ask_a_person"]["available"]
    assert not (keys_in(body) & FORBIDDEN_KEYS)


def test_no_bulk_approve_route(client):
    paths = set(client.app.openapi()["paths"])
    assert "/findings/{finding_id}/review" in paths
    assert not [p for p in paths if "bulk" in p or "approve-all" in p or "batch" in p]


def test_rate_limit(store, settings, stub_llm, officer):
    limiter.reset()
    tight = settings.model_copy(update={"rate_limit_per_minute": 3})
    c = TestClient(create_app(store=store, settings=tight, llm_factory=lambda s, c: stub_llm))
    h = token(tight, officer.user_id)
    codes = [c.get("/applications", headers=h).status_code for _ in range(5)]
    limiter.reset()
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


def test_evaluation_latest(client, settings, officer, store, stub_llm):
    from eval.harness import run_evaluation

    h = token(settings, officer.user_id)
    assert client.get("/evaluation/latest", headers=h).json()["evaluation_run"] is None
    run_evaluation(store, stub_llm, settings, provider_label="offline stub")
    latest = client.get("/evaluation/latest", headers=h).json()["evaluation_run"]
    assert "twin_consistency_rate" in latest["summary"] and latest["report_markdown"].startswith("# Evaluation report")
