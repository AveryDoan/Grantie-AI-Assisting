"""Demo mode: available only when APP_MODE=demo, never otherwise."""

from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.config import Settings
from app.main import create_app


def test_demo_login_absent_in_normal_mode(store, settings):
    client = TestClient(create_app(store=store, settings=settings))
    assert client.get("/health").json()["mode"] == "supabase"
    assert client.post("/demo/login", json={"role": "officer"}).status_code == 404


def test_demo_mode_end_to_end():
    limiter.reset()
    client = TestClient(create_app(settings=Settings(_env_file=None, app_mode="demo", llm_provider="stub", require_redaction_approval=False)))
    assert client.get("/health").json()["mode"] == "demo"
    login = client.post("/demo/login", json={"role": "officer"})
    assert login.status_code == 200
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/me", headers=h).json()
    assert me["role"] == "officer" and "fictional" in me["display_name"]
    queue = client.get("/applications", headers=h).json()
    # 15 evaluation cases + 10 consistency cases (F01 to F10) + 4 document cases (DA to DD)
    assert len(queue) == 29 and all(q["reference"].startswith("APP-") for q in queue)
    assert sum("flags_to_check" in q for q in queue) == 29
    first = queue[0]["id"]
    assert client.post(f"/applications/{first}/assess", headers=h, json={}).status_code == 200
    detail = client.get(f"/applications/{first}", headers=h).json()
    assert detail["application"]["applicant_name"] and detail["findings"]
    audit = client.get("/audit-log", headers=h).json()
    assert audit and audit[0]["actor_name"]
    latest = client.get("/evaluation/latest", headers=h).json()["evaluation_run"]
    assert "stub" in latest["summary"]["provider"] and latest["summary"]["twin_groups"]
    assert client.post("/demo/login", json={"role": "superuser"}).status_code == 422
