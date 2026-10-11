"""The guided flow: 1 Documents, 2 Redaction check, 3 Assessment, 4 Outcome.

- A step is unlocked only when the previous step is Done. The officer can go back and view any finished step.
- Steps 1 and 2 are recorded (application_steps). Step 3 is Done when every rule is decided and every merit criterion is marked
  or Not assessed; step 4 is Done when the application is signed off. Both follow from the data, so they cannot drift.
- Editing a finished step reopens the later steps and leaves the notice "Later steps need to be checked again."
- Every change of a step is written to the audit log (officer, time, step, from and to).
- The AI may not read an application until step 2 is approved (checked in `ai_allowed`).
- An application that was assessed before this flow existed (no step records) is treated as having finished steps 1 and 2.
Nothing here scores, ranks or decides anything: it only says where the officer is and what is still open.
"""

from __future__ import annotations

from typing import Any

from app.services.access import Actor
from app.services.audit import write_audit
from app.services.errors import Conflict
from app.services.review import latest_run, undecided_rules
from app.store.base import Store, now_iso, one

KEYS = {1: "documents", 2: "redaction", 3: "assessment", 4: "outcome"}
TITLES = {1: "Documents", 2: "Redaction check", 3: "Assessment", 4: "Outcome"}
LABEL = {"not_started": "Not started", "in_progress": "In progress", "done": "Done", "waiting": "Waiting for applicant"}
NOTICE = "Later steps need to be checked again."


def rows(store: Store, application_id: str) -> dict[int, dict[str, Any]]:
    return {r["step"]: r for r in store.select("application_steps", eq={"application_id": application_id})}


def is_legacy(store: Store, app: dict[str, Any], recorded: dict[int, dict[str, Any]] | None = None) -> bool:
    """Assessed (or signed off) before the guided flow existed: steps 1 and 2 are taken as done."""
    recorded = rows(store, app["id"]) if recorded is None else recorded
    return not recorded and (app["status"] == "signed_off" or latest_run(store, app["id"]) is not None)


def set_step(store: Store, actor: Actor, app: dict[str, Any], step: int, status: str, *, notice: str | None = None,
             detail: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    recorded = rows(store, app["id"])
    row = recorded.get(step)
    old = row["status"] if row else "not_started"
    if row and old == status and (row.get("notice") or None) == notice and detail is None:
        return row
    values: dict[str, Any] = {"status": status, "notice": notice, "updated_by": actor.user_id}
    if detail is not None:
        values["detail"] = {**(row.get("detail") if row else {}), **detail}
    if row:
        row = store.update("application_steps", values, eq={"id": row["id"]})[0]
    else:
        row = store.insert("application_steps", {"application_id": app["id"], "step": step, **values})[0]
    if old != status:
        write_audit(store, actor, "step.changed", application_id=app["id"], reason=reason,
                    details={"step": step, "key": KEYS[step], "from": old, "to": status, "notice": bool(notice)})
    return row


def reopen_later(store: Store, actor: Actor, app: dict[str, Any], step: int, settings: Any = None) -> None:
    """The officer edited `step`: every later step needs checking again."""
    current = {s["step"]: s for s in state(store, app, settings)["steps"]}
    for later in range(step + 1, 5):
        if current[later]["status"] != "not_started" or later == step + 1:
            set_step(store, actor, app, later, "not_started", notice=NOTICE, reason="an earlier step was edited")


def touch(store: Store, actor: Actor, app: dict[str, Any], step: int, settings: Any = None) -> None:
    """An officer action inside `step`. First action starts it; an edit to a finished step reopens it and the later ones."""
    recorded = rows(store, app["id"])
    if is_legacy(store, app, recorded) and step <= 2:
        # Adopt the legacy application into the flow: steps 1 and 2 stay done until something is edited.
        for s in (1, 2):
            set_step(store, actor, app, s, "done", reason="assessed before the guided flow")
        recorded = rows(store, app["id"])
    row = recorded.get(step)
    was = row["status"] if row else "not_started"
    if was in ("done", "waiting"):
        set_step(store, actor, app, step, "in_progress", reason="edited after it was finished")
        reopen_later(store, actor, app, step, settings)
    elif was == "not_started":
        set_step(store, actor, app, step, "in_progress")


def _open_request(store: Store, app: dict[str, Any]) -> dict[str, Any] | None:
    sent = [r for r in store.select("clarification_requests", eq={"application_id": app["id"], "kind": "documents", "status": "sent"})
            if not r.get("resubmitted_at")]
    return max(sent, key=lambda r: r.get("sent_at") or "", default=None) if app["status"] == "awaiting_applicant" else None


def state(store: Store, app: dict[str, Any], settings: Any = None) -> dict[str, Any]:
    from app.services import doc_step, redaction_check

    recorded = rows(store, app["id"])
    legacy = is_legacy(store, app, recorded)
    run = latest_run(store, app["id"])
    out: list[dict[str, Any]] = []

    def rec(step: int) -> dict[str, Any]:
        return recorded.get(step) or {}

    # 1 Documents
    s1 = "done" if legacy else rec(1).get("status", "not_started")
    open_req = _open_request(store, app)
    missing1 = [] if s1 == "done" else doc_step.missing_for_step1(store, app)
    if open_req and not legacy:
        s1, missing1 = "waiting", ["Waiting for the applicant to reply to your request"] + missing1
    out.append({"step": 1, "missing": missing1, "status": s1, "unlocked": True, "notice": rec(1).get("notice")})

    # 2 Redaction check
    s2 = "done" if legacy else rec(2).get("status", "not_started")
    unlocked2 = s1 == "done"
    if not unlocked2:
        s2 = "not_started"
    out.append({"step": 2, "unlocked": unlocked2, "status": s2, "notice": rec(2).get("notice"),
                "missing": [] if s2 == "done" else (redaction_check.approval_blockers(store, app) if unlocked2 else ["Finish step 1 first"])})

    # 3 Assessment (follows from the data)
    unlocked3 = s2 == "done"
    pending: list[str] = []
    if unlocked3 and run is not None:
        _, pending = undecided_rules(store, app["id"])
    if not unlocked3 or (run is None and not app.get("manual_assessment_requested")):
        s3 = "not_started"
    else:
        s3 = "done" if (run is not None and not pending) or (run is None and app.get("manual_assessment_requested")) else "in_progress"
    miss3: list[str] = []
    if not unlocked3:
        miss3 = ["Approve step 2 first"]
    elif run is None and not app.get("manual_assessment_requested"):
        miss3 = ["The AI check has not run yet"]
    elif pending:
        miss3 = [f"{len(pending)} rule{'s' if len(pending) != 1 else ''} still need{'s' if len(pending) == 1 else ''} your decision or mark"]
    out.append({"step": 3, "unlocked": unlocked3, "status": s3, "notice": rec(3).get("notice"), "missing": miss3, "pending_rules": pending})

    # 4 Outcome
    unlocked4 = s3 == "done"
    s4 = "done" if app["status"] == "signed_off" else ("in_progress" if unlocked4 else "not_started")
    out.append({"step": 4, "unlocked": unlocked4, "status": s4, "notice": rec(4).get("notice"),
                "missing": [] if s4 == "done" else ([] if unlocked4 else ["Finish step 3 first"])})

    for s in out:
        s.update({"key": KEYS[s["step"]], "title": TITLES[s["step"]], "label": LABEL[s["status"]]})
        if s["status"] == "done":
            s["notice"] = None
    current = next((s["step"] for s in out if s["status"] != "done"), 4)
    return {"steps": out, "current": current, "legacy": legacy}


def require_unlocked(store: Store, app: dict[str, Any], step: int, settings: Any = None) -> None:
    st = state(store, app, settings)["steps"][step - 1]
    if not st["unlocked"]:
        raise Conflict(f"Finish step {step - 1} first: {TITLES[step - 1]} must be Done before {TITLES[step]}.")


def ai_allowed(store: Store, app: dict[str, Any], settings: Any) -> bool:
    """The AI may read this application only after the redaction check is approved (or when the flow is switched off)."""
    if not getattr(settings, "require_redaction_approval", True):
        return True
    recorded = rows(store, app["id"])
    if is_legacy(store, app, recorded):
        return True
    return recorded.get(2, {}).get("status") == "done" and recorded.get(1, {}).get("status") == "done"


def assert_ai_allowed(store: Store, actor: Actor, app: dict[str, Any], settings: Any) -> None:
    if not ai_allowed(store, app, settings):
        write_audit(store, actor, "assessment.refused", application_id=app["id"], details={"reason": "redaction_not_approved"})
        raise Conflict("The AI cannot read this application until you approve the redaction check (step 2).")


def complete_documents(store: Store, actor: Actor, app: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Step 1 -> Done, then run the (non-AI) redaction so step 2 has something to check."""
    from app.services import doc_step, redaction_service

    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    missing = doc_step.missing_for_step1(store, app)
    if missing or _open_request(store, app):
        raise Conflict("Step 1 is not finished yet", details={"missing": missing or ["Waiting for the applicant to reply"]})
    set_step(store, actor, app, 1, "done", notice=None)
    redaction_service.run_and_store(store, actor, app["id"], settings, force=True)
    set_step(store, actor, app, 2, "in_progress", notice=rows(store, app["id"]).get(2, {}).get("notice"))
    return state(store, app, settings)
