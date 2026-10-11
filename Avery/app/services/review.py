"""Officer workflow (pipeline step 9), enforced server-side.

- A finding is confirmed, overridden, or sent back to the applicant.
- Override needs a reason; any final status of "Not met" needs a reason.
- "Evidence only" or invalid findings cannot be confirmed: the officer
  records their own decision as an override with a reason.
- Sign-off is rejected until every rule has a finding in the latest run and
  every finding's latest review is a decision (confirm/override).
- There is no bulk approval anywhere. Every action writes to audit_log.
"""

from __future__ import annotations

import logging

from typing import Any, get_args

from app.domain import DecisionStatus, ReviewAction
from app.services.access import Actor, application_for_applicant, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, SignOffBlocked, ValidationFailed
from app.store.base import Store, now_iso, one

log = logging.getLogger(__name__)

SIGN_OFF_STATEMENT = (
    "I have personally reviewed every finding for this application. The AI output was a suggestion only; "
    "the decision recorded here is my own."
)


def latest_run(store: Store, application_id: str) -> dict[str, Any] | None:
    return one(
        store.select(
            "assessment_runs",
            eq={"application_id": application_id, "status": "complete"},
            order="finished_at",
            desc=True,
            limit=1,
        )
    )


def latest_reviews(store: Store, finding_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Latest review per finding, by the strictly increasing `seq`."""
    out: dict[str, dict[str, Any]] = {}
    for r in store.select("officer_reviews", in_={"finding_id": finding_ids}) if finding_ids else []:
        cur = out.get(r["finding_id"])
        if cur is None or r["seq"] > cur["seq"]:
            out[r["finding_id"]] = r
    return out


def _in_guided_flow(store: Store, app: dict[str, Any]) -> bool:
    from app.services import steps

    return bool(steps.rows(store, app["id"]))


def undecided_rules(store: Store, application_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    """(latest run, rule codes still lacking an officer decision).

    A merit criterion is decided only by the officer's mark (or "Not assessed"); every other rule by a review."""
    run = latest_run(store, application_id)
    if run is None:
        return None, []
    rules = store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})
    findings = store.select("findings", eq={"run_id": run["id"]})
    by_rule = {f["rule_id"]: f for f in findings}
    reviews = latest_reviews(store, [f["id"] for f in findings])
    marks = {m["rule_code"] for m in store.select("merit_marks", eq={"application_id": application_id})}
    pending = []
    for rule in sorted(rules, key=lambda r: (r.get("display_order", 0), r["rule_code"])):
        f = by_rule.get(rule["id"])
        if (rule.get("params") or {}).get("section") == "merit":
            if rule["rule_code"] not in marks:
                pending.append(rule["rule_code"])
            continue
        review = reviews.get(f["id"]) if f else None
        if f is None or review is None or review["action"] == "ask_applicant":
            pending.append(rule["rule_code"])
    return run, pending


def review_finding(
    store: Store,
    actor: Actor,
    finding_id: str,
    *,
    action: ReviewAction,
    final_status: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    require_role(actor, "officer")
    finding = one(store.select("findings", eq={"id": finding_id}, limit=1))
    if not finding:
        raise NotFound("Finding not found")
    app = application_for_staff(store, actor, finding["application_id"])
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off; findings can no longer be reviewed")
    run = latest_run(store, app["id"])
    if not run or run["id"] != finding["run_id"]:
        raise Conflict("Only findings from the latest complete assessment run can be reviewed")

    reason = reason.strip() if reason and reason.strip() else None
    ai_status = finding["ai_status"]

    if action == "ask_applicant":
        if final_status is not None:
            raise ValidationFailed("ask_applicant records no final status")
    else:
        if action == "confirm":
            if ai_status == "Evidence only":
                raise ValidationFailed(
                    'This finding is "Evidence only": there is no AI status to confirm. '
                    "Record your decision as an override with a reason."
                )
            if not finding["is_valid"]:
                raise ValidationFailed(
                    "This finding failed verification (quote not found or AI error) and cannot be confirmed. "
                    "Override it with your own decision and a reason."
                )
            final_status = final_status or ai_status
            if final_status != ai_status:
                raise ValidationFailed(f'Confirm keeps the AI status ("{ai_status}"). Use override to change it.')
        if final_status not in get_args(DecisionStatus):
            raise ValidationFailed(f"final_status must be one of {', '.join(get_args(DecisionStatus))}")
        if action == "override" and not reason:
            raise ValidationFailed("An override requires a typed reason")
        if final_status == "Not met" and not reason:
            raise ValidationFailed('A final status of "Not met" always requires a typed reason')

    # Idempotent: repeating the current decision (double click, retry) records nothing new.
    current = latest_reviews(store, [finding_id]).get(finding_id)
    if current and (current["action"], current["final_status"], current.get("reason")) == (action, final_status, reason):
        return current

    review = store.insert(
        "officer_reviews",
        {
            "finding_id": finding_id,
            "officer_id": actor.user_id,
            "action": action,
            "final_status": final_status,
            "reason": reason,
        },
    )[0]
    overridden = action == "override" and ai_status != "Evidence only" and final_status != ai_status
    write_audit(
        store,
        actor,
        "finding.review",
        application_id=app["id"],
        rule_id=finding["rule_id"],
        ai_suggestion=ai_status,
        officer_decision=final_status if action != "ask_applicant" else "ask_applicant",
        overridden=overridden,
        reason=reason,
        rule_pack_version=run["rule_pack_version"],
        details={"finding_id": finding_id, "review_id": review["id"], "action": action, "ai_valid": finding["is_valid"]},
    )
    if final_status == "Not met" and _in_guided_flow(store, app):
        # Confirming a rule as Not met drafts the decline letter at once. It is only a draft: nothing is released
        # until the officer approves it, and approval needs sign-off.
        from app.services import letters

        try:
            letters.draft_on_confirmation(store, actor, app["id"])
        except Exception:   # a letter problem must never undo the officer's decision
            log.warning("could not draft the decline letter for an application after a Not met decision")
    return review


def sign_off(store: Store, actor: Actor, application_id: str, *, statement_acknowledged: bool) -> dict[str, Any]:
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if not statement_acknowledged:
        raise ValidationFailed("You must acknowledge the sign-off statement", details={"statement": SIGN_OFF_STATEMENT})
    if store.select("sign_offs", eq={"application_id": application_id}, limit=1):
        raise Conflict("This application has already been signed off")
    if app["status"] == "draft":
        raise Conflict("A draft application cannot be signed off")
    if store.select("assessment_runs", eq={"application_id": application_id, "status": "running"}, limit=1):
        raise Conflict("An assessment run is still in progress")

    from app.services import steps

    recorded = steps.rows(store, application_id)
    if recorded and not (recorded.get(1, {}).get("status") == "done" and recorded.get(2, {}).get("status") == "done"):
        raise SignOffBlocked("Finish the Documents and Redaction check steps before signing off")
    run, pending = undecided_rules(store, application_id)
    if run is None and not app.get("manual_assessment_requested"):
        raise SignOffBlocked("There is no complete assessment run to sign off")
    if pending:
        raise SignOffBlocked(
            f"{len(pending)} finding(s) still need an officer decision", details={"pending_rules": pending}
        )

    row = store.insert(
        "sign_offs",
        {"application_id": application_id, "officer_id": actor.user_id, "statement_acknowledged": True, "signed_at": now_iso()},
    )[0]
    store.update("applications", {"status": "signed_off"}, eq={"id": application_id})

    decisions: dict[str, int] = {}
    overrides = 0
    if run:
        findings = store.select("findings", eq={"run_id": run["id"]})
        for f_id, rv in latest_reviews(store, [f["id"] for f in findings]).items():
            decisions[rv["final_status"]] = decisions.get(rv["final_status"], 0) + 1
            overrides += rv["action"] == "override"
    write_audit(
        store,
        actor,
        "application.signoff",
        application_id=application_id,
        rule_pack_version=run["rule_pack_version"] if run else None,
        details={
            "sign_off_id": row["id"],
            "statement": SIGN_OFF_STATEMENT,
            "decision_counts": decisions,
            "override_count": overrides,
            "manual_assessment": bool(app.get("manual_assessment_requested")),
        },
    )
    return row


def _default_clarification(rule: dict[str, Any] | None) -> str:
    what = f"this requirement: {rule['rule_text']}" if rule else "part of your application"
    return (
        f"Hello. We are checking your application and need a little more information about {what} "
        "Please reply with the details or a document that shows this. "
        "You can write in your own words; spelling and grammar do not matter. "
        "If you would prefer to talk to a person, reply and ask for a call."
    )


def create_clarification(
    store: Store,
    actor: Actor,
    application_id: str,
    *,
    finding_id: str | None = None,
    message_text: str | None = None,
    send: bool = False,
    clarification_id: str | None = None,
) -> dict[str, Any]:
    """Draft a clarification request, and optionally (mock) send it. No email is sent."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")

    if clarification_id:
        row = one(store.select("clarification_requests", eq={"id": clarification_id, "application_id": application_id}, limit=1))
        if not row:
            raise NotFound("Clarification request not found")
        if row["status"] == "sent":
            raise Conflict("This request has already been sent")
        if message_text:
            row = store.update("clarification_requests", {"message_text": message_text.strip()}, eq={"id": row["id"]})[0]
    else:
        rule = None
        if finding_id:
            finding = one(store.select("findings", eq={"id": finding_id, "application_id": application_id}, limit=1))
            if not finding:
                raise NotFound("Finding not found for this application")
            rule = one(store.select("rules", eq={"id": finding["rule_id"]}, limit=1))
        text = (message_text or "").strip() or _default_clarification(rule)
        row = store.insert(
            "clarification_requests",
            {"application_id": application_id, "finding_id": finding_id, "message_text": text, "created_by": actor.user_id},
        )[0]
        write_audit(store, actor, "clarification.drafted", application_id=application_id,
                    rule_id=rule["id"] if rule else None, details={"clarification_id": row["id"]})

    if send:
        row = store.update("clarification_requests", {"status": "sent", "sent_at": now_iso()}, eq={"id": row["id"]})[0]
        store.update("applications", {"status": "awaiting_applicant"}, eq={"id": application_id})
        write_audit(store, actor, "clarification.sent_mock", application_id=application_id,
                    details={"clarification_id": row["id"], "delivery": "mock - no email sent"})
    return row


def request_manual_assessment(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    """Applicant opts out of AI assessment. Irreversible; the pipeline will refuse to run."""
    app = application_for_applicant(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has already been decided")
    if not app.get("manual_assessment_requested"):
        app = store.update("applications", {"manual_assessment_requested": True}, eq={"id": application_id})[0]
        write_audit(store, actor, "application.manual_assessment_requested", application_id=application_id,
                    details={"status_at_request": app["status"]})
    return {
        "application_id": application_id,
        "manual_assessment_requested": True,
        "message": "A person will assess your application. AI tools will not be used to check it.",
    }


def reopen(store: Store, actor: Actor, application_id: str, reason: str | None) -> dict[str, Any]:
    """Reopen a signed-off application. It needs a reason. The sign-off is kept in its history and the next one is a new version."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("Reopening needs a reason")
    row = one(store.select("sign_offs", eq={"application_id": application_id}, limit=1))
    if app["status"] != "signed_off" or not row:
        raise Conflict("Only a signed-off application can be reopened")
    version = len(store.select("sign_off_history", eq={"application_id": application_id})) + 1
    store.insert("sign_off_history", {"application_id": application_id, "version": version, "officer_id": row["officer_id"],
                                      "signed_at": row["signed_at"], "reopened_by": actor.user_id, "reopened_at": now_iso(), "reason": reason})
    store.delete("sign_offs", eq={"id": row["id"]})
    store.update("applications", {"status": "in_review"}, eq={"id": application_id})
    write_audit(store, actor, "application.reopened", application_id=application_id, reason=reason,
                details={"version_reopened": version, "sign_off_id": row["id"]})
    return {"reopened_version": version, "status": "in_review"}
