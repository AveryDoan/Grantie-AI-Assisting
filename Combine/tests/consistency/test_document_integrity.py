"""Check 3 (document_integrity): metadata and layout signals. Always weak. Only derived signals are kept."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.main import create_app
from app.pipeline.consistency import document_integrity as di
from app.pipeline.consistency.integrity_signals import classify_software, read_integrity_signals
from seed import data
from seed.fraud.pdfkit import make_pdf, pdf_date
from tests.consistency.conftest import ctx, doc, make_settings
from tests.test_api import token

AUTHOR = "Zed Quillfeather"
LETTER_TEXT = "To the Panel,\nI recommend Zed Quillfeather.\nDate: 20 May 2026\nSignature: [signed]\nLETTERHEAD\n"


def pdf_with(info):
    return make_pdf(["Letterhead", "Date: 20 May 2026", "I recommend this student."], info=info)


def test_signals_are_derived_and_no_raw_metadata_string_is_kept():
    data_ = pdf_with({"Producer": "iLovePDF build 3.2.1-x9", "Creator": "Adobe Photoshop 25.0", "Author": AUTHOR, "CreationDate": pdf_date("2026-10-02"),
                      "ModDate": pdf_date("2026-10-05")})
    sig = read_integrity_signals(data_, text="Date: 20 May 2026\nZed Quillfeather wrote this", applicant_name=AUTHOR)
    assert sig["created"] == "2026-10-02" and sig["modified"] == "2026-10-05"
    assert sig["software_class"] == "editor" and sig["editor"] == "Adobe Photoshop"
    assert sig["author_present"] and sig["author_matches_applicant"] and sig["author_in_text"]
    dump = json.dumps(sig)
    assert AUTHOR not in dump and "Quillfeather" not in dump                     # a person's name is never kept
    assert "3.2.1-x9" not in dump and "25.0" not in dump and "build" not in dump  # nor the raw producer or creator text (only a closed-list label)


def test_a_pdf_without_metadata_and_a_non_pdf_give_no_signals():
    sig = read_integrity_signals(make_pdf(["hello world"]), text="hello world")
    assert sig["created"] is None and sig["software_class"] == "unknown" and not sig["author_present"]
    assert read_integrity_signals(b"plain text", text="plain text") == {"kind": "not_pdf"}
    assert read_integrity_signals(b"%PDF-1.4 broken", text="")["kind"] == "pdf"   # never raises


@pytest.mark.parametrize("producer,expected", [
    ("EPSON Scan 2", "scanner"), ("Microsoft: Print To PDF", "office"), ("Skia/PDF m120", "browser"), ("Quartz PDFContext", "system"),
    ("Sejda", "editor"), ("some obscure tool", "other"), ("", "unknown")])
def test_software_classes_come_from_a_closed_list(producer, expected):
    assert classify_software(producer, None)[0] == expected


def _letter(sig, text=LETTER_TEXT):
    return doc("l", "referee_letter", text, signals=sig, label="Referee letter 1")


def test_created_after_the_printed_date_and_editing_software_are_weak_flags():
    sig = {"kind": "pdf", "created": "2026-10-02", "modified": "2026-10-03", "software_class": "editor", "editor": "Adobe Photoshop"}
    flags = di.run(ctx([_letter(sig)]))
    assert {f.check_id for f in flags} == {"document_integrity.created_after_dated", "document_integrity.editing_software"}
    assert all(f.strength == "weak" and f.type == "document_integrity" for f in flags)
    assert all("common" in f.description or "look the same" in f.description for f in flags)   # each states its innocent explanation


def test_a_pdf_made_before_the_date_printed_on_it_and_a_late_edit():
    sig = {"kind": "pdf", "created": "2026-01-02", "modified": "2026-03-20", "software_class": "other"}
    assert {f.check_id for f in di.run(ctx([_letter(sig)]))} == {"document_integrity.created_before_dated", "document_integrity.modified_after_created"}


def test_a_letter_authored_by_the_applicant_is_a_weak_flag():
    sig = {"kind": "pdf", "created": "2026-05-20", "software_class": "office", "author_matches_applicant": True}
    (f,) = di.run(ctx([_letter(sig)]))
    assert f.check_id == "document_integrity.author_is_applicant" and f.strength == "weak"


def test_an_ordinary_office_pdf_made_on_the_day_raises_nothing():
    assert di.run(ctx([_letter({"kind": "pdf", "created": "2026-05-20", "modified": "2026-05-20", "software_class": "office"})])) == []


def test_missing_signature_or_letterhead_is_only_checked_where_the_form_requires_it():
    bare = "To the Panel,\nI recommend this student warmly.\nDate: 20 May 2026\n"
    assert di.run(ctx([_letter({}, bare)])) == []
    flags = di.run(ctx([_letter({}, bare)], letters_need_marks=True))
    assert {f.check_id for f in flags} == {"document_integrity.missing_signature", "document_integrity.missing_letterhead"}
    assert all(f.strength == "weak" and "by eye" in f.description for f in flags)
    assert di.run(ctx([_letter({}, LETTER_TEXT)], letters_need_marks=True)) == []


# ---------------------------------------------------------------- what intake keeps


@pytest.fixture
def client_env(store, settings, applicant_actor, officer):
    limiter.reset()
    s = settings.model_copy(update={"consistency_layer": True})
    return {"client": TestClient(create_app(store=store, settings=s)), "store": store, "a": token(s, applicant_actor.user_id)}


def _draft_and_upload(env, body: bytes, name="letter.pdf"):
    c, h = env["client"], env["a"]
    app_id = c.post("/me/applications", headers=h, json={"grant_program_id": data.SNT_PROGRAM,
                    "fields": {"applicant_name": AUTHOR}, "answers": {}}).json()["id"]
    r = c.post(f"/me/applications/{app_id}/documents", headers=h, json={
        "file_name": name, "declared_type": "referee letter", "content_base64": base64.b64encode(body).decode()})
    assert r.status_code == 200, r.text
    return app_id, env["store"].select("documents", eq={"application_id": app_id})[0]


def test_intake_reads_metadata_before_stripping_and_keeps_only_derived_signals(client_env):
    pdf = pdf_with({"Producer": "iLovePDF build 3.2.1-x9", "Author": AUTHOR, "CreationDate": pdf_date("2026-10-02"), "ModDate": pdf_date("2026-10-02")})
    app_id, row = _draft_and_upload(client_env, pdf)
    sig = row["integrity_signals"]
    assert sig["created"] == "2026-10-02" and sig["software_class"] == "editor" and sig["author_matches_applicant"] is True
    stored = client_env["store"].files[("application-documents", row["storage_path"])]
    assert AUTHOR.encode() not in stored and b"3.2.1-x9" not in stored and b"/Author" not in stored   # metadata stripped from the stored file
    everything = json.dumps({k: v for k, v in row.items() if k != "extracted_text"}) + json.dumps(client_env["store"].select("audit_log"))
    assert AUTHOR not in everything and "3.2.1-x9" not in everything                                  # nor kept in any row or audit entry


def test_with_the_layer_off_intake_stores_no_signals(store, settings, applicant_actor, officer):
    limiter.reset()
    env = {"client": TestClient(create_app(store=store, settings=settings)), "store": store, "a": token(settings, applicant_actor.user_id)}
    _, row = _draft_and_upload(env, pdf_with({"Producer": "iLovePDF", "CreationDate": pdf_date("2026-10-02")}))
    assert row["integrity_signals"] == {}
