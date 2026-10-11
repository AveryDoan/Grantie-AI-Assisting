"""Step 1: Documents.

- Each document slot (Confirmation of Enrolment, evidence of arrival, two letters, biography and headshot, other) gets the
  officer's decision: Confirm, Mark as wrong slot, Request again, or Not needed (with a reason).
- Checks on the files themselves are plain code (no AI): the type, the fields read from the document, and a plain reason when
  something needs a look. They are stored on the document and refreshed here.
- "Ask applicant for more": the officer picks what is missing or unclear; the system DRAFTS a plain-English request naming each
  item and why it is needed. The officer edits it and approves it. Nothing is sent until the officer approves and sends it.
- When a request is sent the application is "Waiting for applicant". A reply (new files) is matched to the files it replaces so
  the old and new versions can be compared.
- Step 1 is Done when every required slot is confirmed, or the officer has recorded why it is not needed.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.documents import check_documents
from app.services.access import Actor, application_for_applicant, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.store.base import Store, now_iso, one

SLOT_LABEL = {"coe": "Confirmation of Enrolment", "arrival": "Evidence of arrival date", "letter1": "Letter of Support #1",
              "letter2": "Letter of Support #2", "bio": "Biography and headshot", "other": "Other supporting documents"}
SLOTS = list(SLOT_LABEL)
ARRIVAL = {"travel_booking", "flight_screenshot", "booking", "itinerary", "travel booking"}
COE = {"coe", "confirmation of enrolment", "enrolment"}
LETTER = {"referee_letter", "referee letter", "reference letter"}
BIO = {"headshot", "photo", "biography"}
# What each slot is for, in plain words (used in the request to the applicant).
WHY = {
    "coe": "It shows your course, your education provider and the day your course starts. We check these against your form.",
    "arrival": "It shows the day you plan to arrive in the Northern Territory.",
    "letter1": "A letter of support tells us about you from a person who knows you.",
    "letter2": "A second letter of support tells us about you from another person who knows you.",
    "bio": "We ask for a short biography and a clear photo of your face.",
    "other": "It helps us understand your application.",
}
DECISIONS = ("confirmed", "wrong_slot", "request_again", "not_needed")
ISSUES = ("missing", "wrong_slot", "unreadable", "differs", "other")


def _type(d: dict[str, Any]) -> str:
    return (d.get("declared_type") or "").strip().lower()


OTHER = re.compile(r"^other(_\d+)?$")


def label_of(slot: str) -> str:
    if slot in SLOT_LABEL:
        return SLOT_LABEL[slot]
    return f"Other supporting document {slot.split('_')[1]}" if OTHER.match(slot) else slot


def valid_slot(slot: str) -> bool:
    return slot in SLOTS or bool(OTHER.match(slot))


def slot_of(doc_type: str) -> str:
    t = doc_type.strip().lower()
    return "coe" if t in COE else "arrival" if t in ARRIVAL else "letter" if t in LETTER else "bio" if t in BIO else "other"


def live(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The documents that count: a replaced file is kept for comparison but is not the current one."""
    return [d for d in documents if not d.get("superseded")]


def assign(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """slot key -> current document (or None). Same grouping as the review screen."""
    docs = sorted(live(documents), key=lambda d: (d.get("uploaded_at") or "", d["id"]))
    out: dict[str, dict[str, Any] | None] = {k: None for k in SLOTS}
    letters = [d for d in docs if slot_of(_type(d)) == "letter"]
    for k in ("coe", "arrival", "bio"):
        out[k] = next((d for d in docs if slot_of(_type(d)) == k), None)
    out["letter1"], out["letter2"] = (letters + [None, None])[:2]
    others = [d for d in docs if slot_of(_type(d)) == "other"]
    out["other"] = others[0] if others else None
    for i, d in enumerate(others[1:9], start=2):        # more than one other document: one row each
        out[f"other_{i}"] = d
    return out


def required_slots(store: Store, app: dict[str, Any]) -> list[str]:
    """Which slots the rule pack needs, from the rules' required documents (data, not code)."""
    pack_rules = store.select("rules", eq={"rule_pack_id": app["rule_pack_id"]}) if app.get("rule_pack_id") else []
    counts: dict[str, int] = {}
    for r in pack_rules:
        wanted: dict[str, int] = {}
        for d in (r.get("params") or {}).get("required_documents", []):
            wanted[d] = wanted.get(d, 0) + 1
        for d, n in wanted.items():
            counts[d] = max(counts.get(d, 0), n)
    need: list[str] = []
    for d, n in counts.items():
        s = slot_of(d)
        if s == "letter":
            need += [f"letter{i}" for i in range(1, min(n, 2) + 1)]
        elif s in ("coe", "arrival", "bio"):
            need.append(s)
    return [k for k in SLOTS if k in need]


def refresh_checks(store: Store, app: dict[str, Any]) -> None:
    """Plain-code checks on the files (type, fields, reasons). No AI, no redaction needed."""
    docs = live(store.select("documents", eq={"application_id": app["id"]}))
    typed = {**((app.get("application_text") or {}).get("fields") or {})}
    for chk in check_documents(typed, docs).checks:
        store.update("documents", {
            "detected_type": chk.detected_type, "type_matches": chk.type_matches, "extracted_fields": chk.extracted_fields,
            "needs_verification": chk.needs_verification, "verification_notes": chk.comparisons,
            "attention_level": chk.attention_level, "attention_reason": chk.attention_reason,
        }, eq={"id": chk.document_id})


def decisions(store: Store, application_id: str) -> dict[str, dict[str, Any]]:
    return {r["slot"]: r for r in store.select("document_reviews", eq={"application_id": application_id})}


def _issue(slot: str, doc: dict[str, Any] | None, decision: dict[str, Any] | None, docs: list[dict[str, Any]] | None = None) -> tuple[str, str] | None:
    """(issue kind, plain reason) when this slot needs asking about, else None."""
    if decision and decision["decision"] == "request_again":
        return "other", "You asked to see this document again"
    if doc is None:
        return "missing", f"No {label_of(slot)} was uploaded"
    if slot == "bio" and _type(doc) == "biography" and docs is not None and not any(_type(d) in ("headshot", "photo") for d in live(docs)):
        return "missing", "A biography was uploaded, but no headshot photo"
    if decision and decision["decision"] == "wrong_slot":
        return "wrong_slot", "This file is not the right kind of document for this place"
    if doc.get("type_matches") is False:
        return "wrong_slot", doc.get("attention_reason") or "This file looks like a different kind of document"
    if doc.get("attention_level") == "attention":
        return "differs", doc.get("attention_reason") or "Something does not match your form"
    if doc.get("extraction_status") in ("no_text", "unsupported") and slot != "bio":
        return "unreadable", "No readable text in this file"
    return None


def missing_for_step1(store: Store, app: dict[str, Any]) -> list[str]:
    docs = store.select("documents", eq={"application_id": app["id"]})
    by_slot, dec = assign(docs), decisions(store, app["id"])
    out = []
    for slot in required_slots(store, app):
        d = dec.get(slot)
        ok = d and ((d["decision"] == "confirmed" and by_slot[slot] is not None and by_slot[slot]["id"] == d.get("document_id"))
                    or d["decision"] == "not_needed")
        if not ok:
            out.append(f"{SLOT_LABEL[slot]}: confirm it, or record why it is not needed")
    return out


def view(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    """Everything the Documents step shows: slots with the officer's decision, what could be asked for, requests, replacements."""
    app = application_for_staff(store, actor, application_id)
    refresh_checks(store, app)
    docs = store.select("documents", eq={"application_id": application_id})
    by_slot, dec = assign(docs), decisions(store, application_id)
    need = set(required_slots(store, app))
    by_id = {d["id"]: d for d in docs}
    slots = []
    for key in by_slot:
        d = by_slot[key]
        issue = _issue(key, d, dec.get(key), docs)
        old = by_id.get(d["replaces_id"]) if d and d.get("replaces_id") else None
        slots.append({
            "slot": key, "label": label_of(key), "required": key in need, "document_id": d["id"] if d else None,
            "file_name": d["file_name"] if d else None, "decision": dec[key]["decision"] if key in dec else None,
            "reason": dec[key].get("reason") if key in dec else None,
            "issue": {"kind": issue[0], "reason": issue[1]} if issue else None,
            "replaces": None if not old else {
                "old": {"file_name": old["file_name"], "text": old.get("extracted_text"), "fields": old.get("extracted_fields") or {}},
                "new": {"file_name": d["file_name"], "text": d.get("extracted_text"), "fields": d.get("extracted_fields") or {}},
            },
        })
    requests = sorted(store.select("clarification_requests", eq={"application_id": application_id, "kind": "documents"}),
                      key=lambda r: r.get("created_at") or "", reverse=True)
    return {
        "slots": slots,
        "suggested_items": [{"slot": s["slot"], "label": s["label"], "kind": s["issue"]["kind"], "reason": s["issue"]["reason"]}
                            for s in slots if s["issue"] and (s["required"] or s["document_id"])],
        "requests": [{k: r.get(k) for k in ("id", "status", "message_text", "items", "created_at", "sent_at", "approved_at", "resubmitted_at")}
                     for r in requests],
        "missing": missing_for_step1(store, app),
    }


def decide(store: Store, actor: Actor, application_id: str, slot: str, decision: str, reason: str | None, settings: Any = None) -> dict[str, Any]:
    from app.services import steps

    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    if not valid_slot(slot):
        raise NotFound("Unknown document slot")
    if decision not in DECISIONS:
        raise ValidationFailed("Choose Confirm, Mark as wrong slot, Request again or Not needed")
    reason = (reason or "").strip() or None
    doc = assign(store.select("documents", eq={"application_id": application_id}))[slot]
    if decision in ("confirmed", "wrong_slot") and doc is None:
        raise ValidationFailed("There is no file in this slot")
    if decision == "not_needed" and not reason:
        raise ValidationFailed("Record why this document is not needed")
    old = decisions(store, application_id).get(slot)
    values = {"document_id": doc["id"] if doc else None, "decision": decision, "reason": reason, "officer_id": actor.user_id}
    if old and (old["decision"], old.get("reason"), old.get("document_id")) == (decision, reason, values["document_id"]):
        return old
    row = store.update("document_reviews", values, eq={"id": old["id"]})[0] if old else store.insert(
        "document_reviews", {"application_id": application_id, "slot": slot, **values})[0]
    write_audit(store, actor, "document.decision", application_id=application_id, reason=reason,
                details={"slot": slot, "decision": decision, "from": old["decision"] if old else None,
                         "document_id": values["document_id"]})
    steps.touch(store, actor, app, 1, settings)
    return row


# ---------------------------------------------------------------- the request to the applicant


def compose_request(first_name: str | None, program: str, items: list[dict[str, str]]) -> str:
    """Plain, short sentences for second-language readers. Names each item and says why it is needed."""
    lines = [f"Hello {first_name or 'applicant'},", "",
             f"Thank you for applying for {program}. We checked your documents. We need a little more from you.", ""]
    for i, it in enumerate(items, start=1):
        lines += [f"{i}. {it['label']}", f"What we need: {it['ask']}", f"Why we need it: {it['why']}", ""]
    lines += ["Please send the new files in your application. Your own words are fine. Spelling and grammar do not matter.",
              "If you want to talk to a person, reply and ask for a call.", "", "Study NT Grants Team"]
    return "\n".join(lines)


def _ask(kind: str, label: str, reason: str, note: str | None) -> str:
    if note and note.strip():
        return note.strip()
    return {
        "missing": f"Please send your {label}.",
        "wrong_slot": f"The file you gave us for {label} looks like a different kind of document. Please send the right one.",
        "unreadable": f"We could not read the file for {label}. Please send a clearer copy.",
        "differs": f"{reason}. Please check it. Send a corrected file, or tell us why it is different.",
    }.get(kind, f"Please send {label} again. {reason}.")


def draft_request(store: Store, actor: Actor, application_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """A DRAFT only. It is never sent until the officer approves and sends it."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    if not items:
        raise ValidationFailed("Choose at least one item to ask for")
    refresh_checks(store, app)
    docs = store.select("documents", eq={"application_id": application_id})
    by_slot = assign(docs)
    clean: list[dict[str, str]] = []
    for it in items:
        slot, kind = it.get("slot"), it.get("kind") or "other"
        if not valid_slot(slot) or kind not in ISSUES:
            raise ValidationFailed("Unknown item")
        auto = _issue(slot, by_slot.get(slot), decisions(store, application_id).get(slot), docs)
        reason = (it.get("reason") or (auto[1] if auto else "") or "").strip()
        clean.append({"slot": slot, "label": label_of(slot), "kind": kind, "reason": reason,
                      "ask": _ask(kind, label_of(slot), reason, it.get("note")), "why": WHY.get(slot, WHY["other"])})
    program = (one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}).get("name", "the grant")
    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)) or {}
    name = ((app.get("application_text") or {}).get("fields") or {}).get("applicant_name") or applicant.get("display_name") or ""
    message = compose_request(name.split()[0] if name else None, program, clean)
    row = store.insert("clarification_requests", {
        "application_id": application_id, "message_text": message, "created_by": actor.user_id, "kind": "documents", "items": clean,
    })[0]
    write_audit(store, actor, "request.drafted", application_id=application_id, details={"request_id": row["id"], "items": [i["slot"] for i in clean]})
    return row


def edit_request(store: Store, actor: Actor, request_id: str, message_text: str) -> dict[str, Any]:
    require_role(actor, "officer")
    row = one(store.select("clarification_requests", eq={"id": request_id}, limit=1))
    if not row or row.get("kind") not in ("documents", "evidence"):
        raise NotFound("Request not found")
    application_for_staff(store, actor, row["application_id"])
    if row["status"] == "sent":
        raise Conflict("This request has already been sent")
    if not message_text.strip():
        raise ValidationFailed("The request cannot be empty")
    out = store.update("clarification_requests", {"message_text": message_text.strip()}, eq={"id": request_id})[0]
    write_audit(store, actor, "request.edited", application_id=row["application_id"], details={"request_id": request_id})
    return out


def approve_and_send(store: Store, actor: Actor, request_id: str, settings: Any = None) -> dict[str, Any]:
    """The officer's approval IS the send. No email goes out in this prototype: the request is recorded as sent (mock)."""
    from app.services import steps

    require_role(actor, "officer")
    row = one(store.select("clarification_requests", eq={"id": request_id}, limit=1))
    if not row or row.get("kind") not in ("documents", "evidence"):
        raise NotFound("Request not found")
    app = application_for_staff(store, actor, row["application_id"])
    if row["status"] == "sent":
        raise Conflict("This request has already been sent")
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    now = now_iso()
    out = store.update("clarification_requests", {"status": "sent", "sent_at": now, "approved_by": actor.user_id, "approved_at": now},
                       eq={"id": request_id})[0]
    store.update("applications", {"status": "awaiting_applicant"}, eq={"id": app["id"]})
    write_audit(store, actor, "request.approved_sent", application_id=app["id"],
                details={"request_id": request_id, "items": [i.get("slot") or i.get("rule_code") for i in row.get("items") or []],
                         "delivery": "mock - no email sent"})
    if row.get("kind") == "evidence":
        return out   # a rule-level request does not hold Step 1
    steps.set_step(store, actor, store.select("applications", eq={"id": app["id"]})[0], 1, "waiting", reason="request sent to the applicant")
    return out


# ---------------------------------------------------------------- the applicant's reply


def respond(store: Store, actor: Actor, application_id: str, uploads: list[dict[str, Any]], settings: Any = None) -> dict[str, Any]:
    """The applicant replies to an open request with new files. Each replaces the file in its slot (the old one is kept to compare)."""
    return _respond(store, actor, application_for_applicant(store, actor, application_id), uploads, settings)


def _respond(store: Store, actor: Actor, app: dict[str, Any], uploads: list[dict[str, Any]], settings: Any = None) -> dict[str, Any]:
    from app.services import intake, steps

    application_id = app["id"]
    req = steps._open_request(store, app)
    if not req:
        raise Conflict("There is no open request from the officer")
    if not uploads:
        raise ValidationFailed("Add at least one file")
    docs = store.select("documents", eq={"application_id": application_id})
    added = []
    for up in uploads:
        slot = slot_of(up["declared_type"])
        current = live(store.select("documents", eq={"application_id": application_id}))
        same = [d for d in sorted(current, key=lambda d: d["id"]) if slot_of(_type(d)) == slot]
        old = same[0] if same else None
        if slot == "letter":   # replace the letter the request named, else the first
            wanted = {i["slot"] for i in req.get("items") or []}
            by = assign(docs)
            old = next((by[k] for k in ("letter1", "letter2") if k in wanted and by[k]), old)
        new = intake.store_document(store, actor, app, file_name=up["file_name"], declared_type=up["declared_type"],
                                    content_base64=up["content_base64"], replaces_id=old["id"] if old else None)
        if old:
            store.update("documents", {"superseded": True}, eq={"id": old["id"]})
        added.append(new["id"])
    now = now_iso()
    store.update("clarification_requests", {"resubmitted_at": now}, eq={"id": req["id"]})
    has_run = bool(store.select("assessment_runs", eq={"application_id": application_id}, limit=1))
    store.update("applications", {"status": "in_review" if has_run else "submitted"}, eq={"id": application_id})
    write_audit(store, actor, "request.resubmitted", application_id=application_id,
                details={"request_id": req["id"], "documents": added})
    after_reply(store, actor, application_id, settings)
    return {"resubmitted_at": now, "documents": added}


def after_reply(store: Store, actor: Actor, application_id: str, settings: Any = None) -> None:
    """Officer side effects of a reply: step 1 reopens (the new files need a look) and the later steps need checking again."""
    from app.services import steps

    app = one(store.select("applications", eq={"id": application_id}, limit=1))
    if app is None:
        return
    refresh_checks(store, app)
    docs = store.select("documents", eq={"application_id": application_id})
    for slot, current in assign(docs).items():
        old = decisions(store, application_id).get(slot)
        if current is not None and current.get("replaces_id") and old:
            store.delete("document_reviews", eq={"id": old["id"]})      # a replaced file needs a fresh decision
    steps.set_step(store, actor, app, 1, "in_progress", reason="the applicant replied")
    steps.reopen_later(store, actor, app, 1, settings)


def demo_reply(store: Store, actor: Actor, application_id: str, settings: Any = None) -> dict[str, Any]:
    """DEMO MODE ONLY: stand in for the applicant. Sends a fictional replacement for each item in the open request."""
    import base64

    from seed import data
    from app.services import steps

    app = application_for_staff(store, actor, application_id)
    req = steps._open_request(store, app)
    if not req:
        raise Conflict("There is no open request to reply to")
    name = ((app.get("application_text") or {}).get("fields") or {}).get("applicant_name") or "Sample Applicant"
    made = {
        "coe": ("coe", data.coe(name)), "arrival": ("travel booking", data.booking(name)),
        "letter1": ("referee letter", data.referee(name, "Ms Sample Referee", "Teacher", "Sample School", "Teacher", "3 years", "9 June 2026")),
        "letter2": ("referee letter", data.referee(name, "Mr Sample Referee", "Coordinator", "Sample Clinic", "Supervisor", "2 years", "20 June 2026")),
        "bio": ("headshot", data.headshot()), "other": ("other", "Sample supporting document. SAMPLE: FICTIONAL TEST DOCUMENT"),
    }
    uploads = []
    for it in req.get("items") or []:
        if it["slot"] in made:
            dtype, text = made[it["slot"]]
            uploads.append({"file_name": f"replacement_{it['slot']}.txt", "declared_type": dtype,
                            "content_base64": base64.b64encode((text + "\nSAMPLE: FICTIONAL TEST DOCUMENT\n").encode()).decode()})
    return _respond(store, actor, app, uploads, settings)
