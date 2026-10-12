"""Email composer: one path, nothing sent but by the officer's own Send / Mark as sent; the AI sees no real name or email."""

from __future__ import annotations

import pytest

from app.llm.base import Prompt
from app.services import composer, doc_step
from app.services.errors import Conflict
from tests.conftest import app_id


class SpyLLM:
    def __init__(self):
        self.prompts: list[Prompt] = []

    def call_llm(self, prompt, schema):
        self.prompts.append(prompt)
        return schema(body="Hello [APPLICANT_NAME],\n\nPlease reply with your Confirmation of Enrolment by the due date. It shows your course.\n\nStudy NT Grants Team")


def _draft(store, officer, code="N01", llm=None, note=None):
    return composer.draft(store, officer, app_id(code), picked=[{"slot": "coe"}], custom=["A letter from your provider"], note=note, due_date="2026-11-01", llm=llm)


def test_the_ai_prompt_has_no_real_name_or_email_and_the_name_is_restored_after(store, officer):
    app = store.select("applications", eq={"id": app_id("N01")})[0]
    f = app["application_text"]["fields"]
    spy = SpyLLM()
    d = _draft(store, officer, llm=spy, note=f"Please ask {f['applicant_name']} at {f['email']} for it")
    text = " ".join(p.system + p.user for p in spy.prompts)
    first = f["applicant_name"].split()[0]
    assert f["applicant_name"] not in text and first not in text and f["email"] not in text and "[APPLICANT_NAME]" in text
    assert d["body"].startswith(f"Hello {first},") and d["template"] is False and d["to"] == f["email"]


def test_a_template_is_used_when_the_ai_is_unavailable_or_writes_forbidden_words(store, officer):
    class Down:
        def call_llm(self, *a):
            raise RuntimeError("down")

    class Bad(SpyLLM):
        def call_llm(self, prompt, schema):
            return schema(body="Your merit score is lower than other applicants.")

    for llm in (None, Down(), Bad()):
        d = _draft(store, officer, llm=llm)
        assert d["template"] is True and d["template_note"] == "Template: AI unavailable" and "Confirmation of Enrolment" in d["body"]
        assert not composer.FORBIDDEN.search(d["body"])


def test_send_is_the_only_action_that_sends_and_needs_no_provider_to_mark_as_sent(store, officer):
    d = _draft(store, officer)
    save = lambda **k: dict(subject=d["subject"], body=d["body"], cc=None, items=d["items"], due_date=d["due_date"], request_id=None, **k)
    saved = composer.save(store, officer, app_id("N01"), **save())
    assert saved["status"] == "draft" and store.select("applications", eq={"id": app_id("N01")})[0]["status"] != "awaiting_applicant"
    with pytest.raises(Conflict, match="No email provider"):
        composer.send(store, officer, app_id("N01"), mode="send", **save())
    bad = save() | {"body": d["body"] + "\n[DUE DATE]"}
    with pytest.raises(Conflict, match="DUE DATE"):
        composer.send(store, officer, app_id("N01"), mode="marked_sent", **bad)
    out = composer.send(store, officer, app_id("N01"), mode="marked_sent", **save())
    assert out["status"] == "sent" and out["delivery"] == "marked_sent" and out["due_date"] == "2026-11-01"
    assert store.select("applications", eq={"id": app_id("N01")})[0]["status"] == "awaiting_applicant"
    assert doc_step.decisions(store, app_id("N01"))["coe"]["decision"] == "request_again"
    sent = [a for a in store.select("audit_log") if a["action"] == "request.marked_sent"][0]
    assert sent["details"]["text"] == d["body"] and sent["details"]["due_date"] == "2026-11-01"
    slot = next(s for s in doc_step.view(store, officer, app_id("N01"))["slots"] if s["slot"] == "coe")
    assert slot["requested"]["due_date"] == "2026-11-01"


def test_the_old_request_card_is_gone_from_the_screens():
    from pathlib import Path

    src = "".join(p.read_text(encoding="utf-8") for p in (Path(__file__).resolve().parents[1] / "frontend/src").rglob("*.tsx"))
    assert "Request more documents" not in src and "RequestPanel" not in src and "Draft the request" not in src
