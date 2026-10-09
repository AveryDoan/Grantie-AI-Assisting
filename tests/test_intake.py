"""Applicant intake: draft -> upload -> check -> submit, then the officer's
redaction and AI views on what was uploaded (synthetic data only)."""

from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.ratelimit import limiter  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.llm.stub import OfflineStubProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from seed import data  # noqa: E402
from tests.redaction.pdfs import PNG_1PX, make_pdf  # noqa: E402
from tests.test_api import token  # noqa: E402

FIELDS = {
    "applicant_name": "Mai Fictional", "date_of_birth": "2006-04-02", "email": "mai.fictional@example.invalid",
    "phone": "+60 12 345 6789", "nationality": "Malaysia", "australian_or_nz_citizen_or_pr": "No",
    "residential_address": "10 Sample Road, Sample City, Malaysia", "residential_country": "Malaysia",
    "postal_address": "10 Sample Road, Sample City, Malaysia", "postal_country": "Malaysia",
    "education_provider": "Charles Darwin University", "course_name": "Bachelor of Nursing", "study_load": "Full-time",
    "arrival_date": "2026-11-20", "under_18": "No", "declaration_agreed": "Yes", "declaration_name": "Mai Fictional",
    "declaration_date": "2026-10-09",
}
ANSWERS = {"current_study": "I am finishing secondary school in Malaysia.",
           "nt_contribution": "I want to work in remote clinics in the Northern Territory.",
           "leadership": "I lead the school health club."}
COE = ["SAMPLE DOCUMENT - FICTIONAL", "Confirmation of Enrolment", "Provider: Charles Darwin University",
       "Student Name: Mai Fictional", "Course: Bachelor of Nursing", "Course Start Date: 5 October 2026",
       "Study Load: Full-time"]
LETTER = ("To whom it may concern,\nI have known Mai Fictional for 3 years as her teacher.\n"
          "Referee name: Ms Rosa Example\nPosition: Teacher\nPhone: +60 3 1234 5678\nDate: 1 May 2026\n")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def env(store, settings, officer, applicant_actor):
    limiter.reset()
    llm = LLMClient(OfflineStubProvider(), temperature=0.0)
    client = TestClient(create_app(store=store, settings=settings, llm_factory=lambda s, c: llm))
    return {"c": client, "store": store, "a": token(settings, applicant_actor.user_id), "o": token(settings, officer.user_id)}


def draft(env) -> str:
    r = env["c"].post("/me/applications", headers=env["a"], json={"grant_program_id": data.SNT_PROGRAM,
                                                                  "fields": FIELDS, "answers": ANSWERS})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def upload(env, app_id: str, name: str, kind: str, content: bytes):
    return env["c"].post(f"/me/applications/{app_id}/documents", headers=env["a"],
                         json={"file_name": name, "declared_type": kind, "content_base64": b64(content)})


def test_full_intake_flow_then_officer_sees_redaction_and_ai_quotes(env):
    c, store = env["c"], env["store"]
    app_id = draft(env)
    pdf = upload(env, app_id, "Mai Fictional CoE.pdf", "coe", make_pdf([COE], info={"/Author": "Mai Fictional"}))
    assert pdf.status_code == 200, pdf.text
    assert pdf.json()["kind"] == "pdf" and pdf.json()["extraction_status"] == "ok" and pdf.json()["looks_like"] == "coe"
    assert upload(env, app_id, "letter.txt", "referee letter", LETTER.encode()).json()["looks_like"] == "referee_letter"
    photo = upload(env, app_id, "me.png", "headshot", PNG_1PX).json()
    assert photo["kind"] == "image:png" and photo["needs_manual_review"]

    # Stored file: no metadata, and the storage path has no file name in it.
    doc = next(d for d in store.select("documents", eq={"application_id": app_id}) if d["declared_type"] == "coe")
    assert "Mai" not in doc["storage_path"]
    assert b"Mai Fictional" not in store.files[("application-documents", doc["storage_path"])].split(b"stream")[0]

    check = c.get(f"/me/applications/{app_id}/check", headers=env["a"]).json()
    assert "academic_achievements" in {m["field"] for m in check["missing_fields"]}
    assert any(m["document_type"] == "travel_booking" for m in check["missing_documents"])
    assert not any(m["document_type"] == "headshot" for m in check["missing_documents"])  # photo: checked by eye
    assert not check["wrong_document_type"]

    # Officers cannot see or touch drafts in the queue, and cannot submit for the applicant.
    assert app_id not in {q["id"] for q in c.get("/applications", headers=env["o"]).json()}
    assert c.post(f"/me/applications/{app_id}/submit", headers=env["o"], json={}).status_code == 403

    sub = c.post(f"/me/applications/{app_id}/submit", headers=env["a"], json={})
    assert sub.status_code == 200 and sub.json()["status"] == "submitted"
    assert upload(env, app_id, "late.txt", "other", b"late").status_code == 409  # read-only once submitted

    assert app_id in {q["id"] for q in c.get("/applications", headers=env["o"]).json()}
    red = c.post(f"/applications/{app_id}/redact", headers=env["o"], json={}).json()
    assert red["report"]["ai_status"] == "ready", red
    report = c.get(f"/applications/{app_id}/redaction-report", headers=env["o"]).json()
    assert {"[PERSON_1]", "[REFEREE_1]"} <= {t["token"] for t in report["tokens"]}
    assert "Mai" not in json.dumps(report) and "Rosa" not in json.dumps(report)

    assert c.post(f"/applications/{app_id}/assess", headers=env["o"], json={}).status_code == 200
    detail = c.get(f"/applications/{app_id}", headers=env["o"]).json()
    assert "Mai" not in detail["application"]["redacted_text"]
    assert detail["application"]["applicant_name"] == "Mai Fictional"  # the name typed on this application
    letter = next(d for d in detail["documents"] if d["declared_type"] == "referee letter")
    assert "Rosa" not in letter["redacted_text"] and "[REFEREE_1]" in letter["redacted_text"]
    assert any(f["evidence_quote"] for f in detail["findings"])

    audit = json.dumps(store.select("audit_log"))
    assert "Mai" not in audit and "CoE.pdf" not in audit and "Rosa" not in audit


def test_upload_rules(env):
    app_id = draft(env)
    assert upload(env, app_id, "big.pdf", "coe", b"%PDF-" + b"0" * (5 * 1024 * 1024)).status_code == 422
    assert upload(env, app_id, "cv.docx", "other", b"PK\x03\x04 word file").status_code == 422
    assert upload(env, app_id, "x.pdf", "passport scan", make_pdf([["x"]])).status_code == 422  # unknown type
    doc = upload(env, app_id, "letter.txt", "referee letter", LETTER.encode()).json()
    r = env["c"].delete(f"/me/applications/{app_id}/documents/{doc['id']}", headers=env["a"])
    assert r.status_code == 200 and env["store"].files == {}


def test_only_the_owner_can_change_a_draft(env, store, settings):
    app_id = draft(env)
    stranger = "00000000-0000-0000-0000-00000000beef"
    store.insert("profiles", {"id": stranger, "role": "applicant"})
    h = token(settings, stranger)
    assert env["c"].put(f"/me/applications/{app_id}", headers=h, json={"fields": {}}).status_code == 404
    assert env["c"].post(f"/me/applications/{app_id}/submit", headers=h, json={}).status_code == 404
    assert env["c"].post("/me/applications", headers=env["o"], json={"fields": {}}).status_code == 403


def test_manual_assessment_choice_is_kept_and_blocks_ai(env):
    app_id = draft(env)
    env["c"].post(f"/me/applications/{app_id}/submit", headers=env["a"], json={"manual_assessment": True})
    r = env["c"].post(f"/applications/{app_id}/assess", headers=env["o"], json={})
    assert r.status_code == 409
