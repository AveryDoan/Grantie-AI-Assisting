"""The email composer: ONE path for every "Ask the applicant for more" (documents, eligibility findings, Needs evidence).

- The officer ticks items (documents flagged, Needs evidence findings, custom text). The body is drafted from those items and
  the officer's note. The AI sees only labels, reasons and the note with the applicant's name, email and other known personal
  values replaced by placeholders; the real name is put back by the server afterwards. If the AI is unavailable the body is a
  fixed template, marked as such.
- Nothing is sent by this app. With no email provider connected the officer copies the email, opens it in their mail app, or
  marks it as sent after sending it themselves. Every send / mark is logged with the text.
- The text never mentions AI-use signals, consistency flags, merit or other applicants (checked in code before it can be sent).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMError, Prompt
from app.services import doc_step, evidence_request
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, ValidationFailed
from app.store.base import Store, now_iso, one

REASONS = {"missing": "Not provided", "wrong_slot": "Wrong document type", "unreadable": "Unclear", "differs": "Unclear",
           "other": "Unclear", "needs_evidence": "Needs evidence"}
FORBIDDEN = re.compile(r"\b(ai[- ]use|ai[- ]generated|detector|consistency|merit|score|rank\w*|other applicants?|redact\w*|fraud\w*|cheat\w*|suspicious|fake)\b", re.I)
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{2,60}\]")
DEFAULT_DUE_DAYS = 14
EMAIL_CONNECTED = False   # no provider is wired in this prototype


class EmailBody(BaseModel):
    body: str


def _fields(app: dict[str, Any]) -> dict[str, Any]:
    return (app.get("application_text") or {}).get("fields") or {}


def _program(store: Store, app: dict[str, Any]) -> str:
    return (one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}).get("name", "the grant")


def _first_name(store: Store, app: dict[str, Any]) -> str:
    name = _fields(app).get("applicant_name") or (one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)) or {}).get("display_name") or ""
    return name.split()[0] if name.split() else ""


def due_default(store: Store, app: dict[str, Any]) -> str:
    pack = one(store.select("rule_packs", eq={"id": app.get("rule_pack_id")}, limit=1)) or {}
    days = int((pack.get("letter_config") or {}).get("request_due_days") or DEFAULT_DUE_DAYS)
    return (date.today() + timedelta(days=days)).isoformat()


def options(store: Store, actor: Actor, application_id: str, preselect_slots: list[str] | None = None) -> dict[str, Any]:
    """Everything the composer shows before drafting: recipient, the tickable items (flagged ones pre-ticked), the default due date."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    docs = store.select("documents", eq={"application_id": application_id})
    by_slot, dec = doc_step.assign(docs), doc_step.decisions(store, application_id)
    items = []
    flagged = set(preselect_slots or [])
    for slot in by_slot:
        issue = doc_step._issue(slot, by_slot[slot], dec.get(slot), docs)
        if issue or slot in flagged:
            items.append({"key": f"slot:{slot}", "kind": "document", "slot": slot, "label": doc_step.label_of(slot),
                          "reason": REASONS.get(issue[0] if issue else "other", "Not provided"), "detail": issue[1] if issue else "You asked to see it again",
                          "ticked": True if (slot in flagged or not flagged) and issue is not None else slot in flagged})
    for e in evidence_request.needs_evidence(store, application_id):
        items.append({"key": f"finding:{e['finding_id']}", "kind": "finding", "finding_id": e["finding_id"], "label": f"Rule {e['rule_code']}",
                      "reason": "Needs evidence", "detail": e["ask"], "ticked": True})
    return {"to": _fields(app).get("email") or "", "subject": f"{_program(store, app)}: more information needed",
            "items": items, "due_date": due_default(store, app), "email_connected": EMAIL_CONNECTED,
            "request": _open_request(store, application_id)}


def _open_request(store: Store, application_id: str) -> dict[str, Any] | None:
    rows = [r for r in store.select("clarification_requests", eq={"application_id": application_id}) if r.get("status") == "sent" and not r.get("resubmitted_at")]
    r = max(rows, key=lambda r: r.get("sent_at") or "", default=None)
    return None if not r else {"id": r["id"], "sent_at": r["sent_at"], "due_date": r.get("due_date"), "subject": r.get("subject"),
                               "message_text": r["message_text"], "slots": [i.get("slot") for i in r.get("items") or [] if i.get("slot")]}


def _resolve(store: Store, application_id: str, picked: list[dict[str, Any]], custom: list[str]) -> list[dict[str, str]]:
    app = one(store.select("applications", eq={"id": application_id}, limit=1))
    docs = store.select("documents", eq={"application_id": application_id})
    by_slot, dec = doc_step.assign(docs), doc_step.decisions(store, application_id)
    ev = {e["finding_id"]: e for e in evidence_request.needs_evidence(store, application_id)}
    out: list[dict[str, str]] = []
    for p in picked:
        if p.get("slot"):
            slot = p["slot"]
            if not doc_step.valid_slot(slot):
                raise ValidationFailed("Unknown document")
            issue = doc_step._issue(slot, by_slot.get(slot), dec.get(slot), docs)
            kind = issue[0] if issue else "other"
            out.append({"slot": slot, "label": doc_step.label_of(slot), "reason": REASONS.get(kind, "Not provided"),
                        "ask": doc_step._ask(kind, doc_step.label_of(slot), issue[1] if issue else "", None),
                        "why": doc_step.WHY.get(slot, doc_step.WHY["other"])})
        elif p.get("finding_id") in ev:
            e = ev[p["finding_id"]]
            out.append({"finding_id": e["finding_id"], "rule_code": e["rule_code"], "label": f"Rule {e['rule_code']}", "reason": "Needs evidence",
                        "ask": e["ask"], "why": e["why"]})
    for text in custom:
        if text.strip():
            out.append({"label": "Other", "reason": "Needs evidence", "ask": text.strip(), "why": "It helps us understand your application."})
    return out


def _mask(text: str, app: dict[str, Any]) -> str:
    """The officer's note with the applicant's name, email and other typed personal values replaced by placeholders."""
    f = _fields(app)
    for key, token in (("applicant_name", "[APPLICANT_NAME]"), ("email", "[APPLICANT_EMAIL]"), ("phone", "[APPLICANT_PHONE]")):
        v = str(f.get(key) or "").strip()
        if v:
            text = re.sub(re.escape(v), token, text, flags=re.I)
            if key == "applicant_name":
                for part in v.split():
                    if len(part) > 2:
                        text = re.sub(rf"\b{re.escape(part)}\b", token, text, flags=re.I)
    return text


def template_body(first_name: str, program: str, items: list[dict[str, str]], due: str, note: str | None) -> str:
    lines = [f"Hello {first_name or 'applicant'},", "", f"Thank you for applying for {program}. We checked your application. We need a little more from you.", ""]
    for i, it in enumerate(items, start=1):
        lines += [f"{i}. {it['label']}", f"   What we need: {it['ask']}", f"   Why we need it: {it['why']}", ""]
    if note and note.strip():
        lines += [note.strip(), ""]
    lines += [f"Please reply to this email with the files or details by {due}.", "You can write in your own words. Spelling and grammar do not matter.",
              "If you would like to talk to a person, reply and ask for a call.", "", "Study NT Grants Team"]
    return "\n".join(lines)


def compose_prompt(items: list[dict[str, str]], note: str, due: str, program: str) -> Prompt:
    """Only labels, reasons and the officer's note (with personal values masked). The real name is added by the server afterwards."""
    listing = "\n".join(f"- {i['label']} ({i['reason']}): {i['ask']} Why: {i['why']}" for i in items)
    return Prompt(
        task="email:request", prompt_version="email-1", rule_pack_version="n/a",
        system=("Write a short, polite, plain-English email asking an applicant for more information. Short sentences, no blame. "
                "Say exactly what is needed, why, how to send it (reply to this email) and the due date. Greet them as [APPLICANT_NAME]. "
                "Do not mention AI, checks of consistency, scores, marks, other applicants or redaction. Return JSON {\"body\": string}."),
        user=f"Grant: {program}\nDue date: {due}\nItems:\n{listing}\nOfficer note: {note or 'none'}",
        cache_text=f"{listing}|{note}|{due}", meta={"offline_template": True},
    )


def draft(store: Store, actor: Actor, application_id: str, *, picked: list[dict[str, Any]], custom: list[str], note: str | None,
          due_date: str | None, llm: Any = None) -> dict[str, Any]:
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    items = _resolve(store, application_id, picked, custom)
    if not items:
        raise ValidationFailed("Tick at least one item to ask for")
    due = due_date or due_default(store, app)
    program, first = _program(store, app), _first_name(store, app)
    body, template = None, True
    if llm is not None:
        try:
            masked = compose_prompt(items, _mask(note or "", app), due, program)
            text = llm.call_llm(masked, EmailBody).body.strip()
            text = text.replace("[APPLICANT_NAME]", first or "applicant")
            if text and not FORBIDDEN.search(text) and "[APPLICANT" not in text:
                body, template = text, False
        except (LLMError, Exception):   # the officer is never blocked: fall back to the template
            body = None
    body = body or template_body(first, program, items, due, note)
    write_audit(store, actor, "request.composed", application_id=application_id, details={"items": len(items), "template": template})
    return {"to": _fields(app).get("email") or "", "subject": f"{program}: more information needed", "body": body, "items": items, "due_date": due,
            "template": template, "template_note": "Template: AI unavailable" if template else None}


def blockers(subject: str, body: str, to: str) -> list[str]:
    out = []
    if not to.strip() or "@" not in to:
        out.append("The applicant has no email address on record")
    if not subject.strip():
        out.append("Add a subject")
    ph = sorted(set(PLACEHOLDER.findall(body + " " + subject)))
    if ph:
        out.append(f"Fill in {', '.join(ph)}")
    if FORBIDDEN.search(body):
        out.append("Remove wording about checks, marks or other applicants")
    return out


def save(store: Store, actor: Actor, application_id: str, *, subject: str, body: str, cc: str | None, items: list[dict[str, Any]],
         due_date: str | None, request_id: str | None, template: bool = False) -> dict[str, Any]:
    """Save a draft (nothing leaves the app)."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    values = {"message_text": body, "subject": subject, "cc_email": cc or None, "items": items, "due_date": due_date or None,
              "to_email": _fields(app).get("email"), "body_template": template}
    if request_id:
        row = one(store.select("clarification_requests", eq={"id": request_id, "application_id": application_id}, limit=1))
        if not row or row["status"] == "sent":
            raise Conflict("This request was already sent and cannot be changed")
        out = store.update("clarification_requests", values, eq={"id": request_id})[0]
    else:
        out = store.insert("clarification_requests", values | {"application_id": application_id, "created_by": actor.user_id, "kind": "documents", "status": "draft"})[0]
    write_audit(store, actor, "request.draft_saved", application_id=application_id, details={"request_id": out["id"]})
    return out


def send(store: Store, actor: Actor, application_id: str, *, subject: str, body: str, cc: str | None, items: list[dict[str, Any]], due_date: str | None,
         request_id: str | None, mode: str, template: bool = False) -> dict[str, Any]:
    """The ONLY action that records a request as sent. `mode` = send (needs a provider) or marked_sent (the officer sent it themselves)."""
    from app.services import steps

    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    if mode == "send" and not EMAIL_CONNECTED:
        raise Conflict("No email provider is connected. Copy the email, open it in your mail app, then choose Mark as sent.")
    if mode not in ("send", "marked_sent"):
        raise ValidationFailed("mode must be send or marked_sent")
    problems = blockers(subject, body, _fields(app).get("email") or "")
    if problems:
        raise Conflict(problems[0], details={"blockers": problems})
    row = save(store, actor, application_id, subject=subject, body=body, cc=cc, items=items, due_date=due_date, request_id=request_id, template=template)
    now = now_iso()
    out = store.update("clarification_requests", {"status": "sent", "sent_at": now, "approved_by": actor.user_id, "approved_at": now,
                                                  "delivery": "sent" if mode == "send" else "marked_sent"}, eq={"id": row["id"]})[0]
    for it in items:   # the document slots are now Requested
        if it.get("slot") and (doc_step.decisions(store, application_id).get(it["slot"]) or {}).get("decision") != "request_again":
            doc_step.decide(store, actor, application_id, it["slot"], "request_again", f"Asked the applicant on {now[:10]}")
    store.update("applications", {"status": "awaiting_applicant"}, eq={"id": application_id})
    write_audit(store, actor, "request.sent" if mode == "send" else "request.marked_sent", application_id=application_id,
                details={"request_id": out["id"], "items": [i.get("slot") or i.get("rule_code") or "custom" for i in items], "due_date": due_date,
                         "to_domain": (out.get("to_email") or "").split("@")[-1], "text": body, "subject": subject})
    if any(i.get("slot") for i in items):
        steps.set_step(store, actor, store.select("applications", eq={"id": application_id})[0], 1, "waiting", reason="request sent to the applicant")
    return out
