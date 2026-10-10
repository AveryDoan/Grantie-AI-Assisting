"""Evidence requests for rules that are Needs evidence. A DRAFT only: the app sends nothing. The officer edits it, copies or
downloads it, and, once they have sent it themselves, sets the application to Waiting for applicant (doc_step.approve_and_send).
The text is plain English: one short sentence per item, saying what is needed and why."""

from __future__ import annotations

from typing import Any

from app.services import doc_step, rule_guidance
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, ValidationFailed
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, one

NOT_SENT = "This draft is not sent from the app."


def needs_evidence(store: Store, application_id: str) -> list[dict[str, Any]]:
    """Findings that are Needs evidence now: the officer's latest decision if there is one, else the AI/code result."""
    run = latest_run(store, application_id)
    if run is None:
        return []
    rules = {r["id"]: r for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})}
    findings = store.select("findings", eq={"run_id": run["id"]})
    reviews = latest_reviews(store, [f["id"] for f in findings])
    docs = store.select("documents", eq={"application_id": application_id})
    out = []
    for f in findings:
        rule = rules.get(f["rule_id"])
        if not rule or (rule.get("params") or {}).get("section") == "merit":
            continue
        rv = reviews.get(f["id"])
        status = (rv.get("final_status") if rv and rv["action"] != "ask_applicant" else None) or ("Needs evidence" if rv else f["ai_status"])
        if rv and rv["action"] == "ask_applicant":
            status = "Needs evidence"
        if status != "Needs evidence":
            continue
        g = rule_guidance.for_rule(rule["rule_code"], docs)
        p = rule.get("params") or {}
        missing_docs = [d["label"] for d in g["rule_documents"] if d["status"] == "Not provided"]
        ask = p.get("what_would_change") or (f"Please send: {', '.join(missing_docs)}." if missing_docs else (g["verify_note"] or "Please send more information."))
        out.append({"finding_id": f["id"], "rule_code": rule["rule_code"], "label": rule["rule_text"], "ask": ask,
                    "why": f"The rule is: {rule['rule_text']}"})
    return sorted(out, key=lambda i: (i["rule_code"][0], int("".join(c for c in i["rule_code"] if c.isdigit()) or 0)))


def compose(first_name: str | None, program: str, items: list[dict[str, str]]) -> str:
    lines = [f"Hello {first_name or 'applicant'},", "",
             f"Thank you for applying for {program}. We need a little more from you before we can finish.", ""]
    for i, it in enumerate(items, start=1):
        lines += [f"{i}. {it['ask']}", f"   Why: {it['why']}", ""]
    lines += ["You can reply in your own words. Spelling and grammar do not matter.",
              "If you want to talk to a person, reply and ask for a call.", "", "Study NT Grants Team"]
    return "\n".join(lines)


def draft(store: Store, actor: Actor, application_id: str, finding_ids: list[str] | None = None) -> dict[str, Any]:
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    items = needs_evidence(store, application_id)
    if finding_ids:
        items = [i for i in items if i["finding_id"] in finding_ids]
    if not items:
        raise ValidationFailed("No rule needs evidence right now")
    program = (one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}).get("name", "the grant")
    fields = ((app.get("application_text") or {}).get("fields") or {})
    name = fields.get("applicant_name") or (one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)) or {}).get("display_name") or ""
    row = store.insert("clarification_requests", {
        "application_id": application_id, "message_text": compose(name.split()[0] if name else None, program, items),
        "created_by": actor.user_id, "kind": "evidence", "items": items,
    })[0]
    write_audit(store, actor, "evidence_request.drafted", application_id=application_id,
                details={"request_id": row["id"], "rules": [i["rule_code"] for i in items], "sent": False})
    return row | {"to": fields.get("email") or "", "subject": f"{program}: more information needed",
                  "not_sent_note": NOT_SENT, "items": items}
