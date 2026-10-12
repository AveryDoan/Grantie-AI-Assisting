"""AI-use check (sub-step 3.3): run the local detector on request, keep the trace, record the officer's action per field.

A WEAK signal and a prompt for a fair conversation. It never changes a rule status, a merit mark, a letter, the panel
summary or the outcome, and it never blocks sign-off: no other module reads these tables (tests/test_ai_use.py checks).
The detector reads REDACTED text only; the follow-up request to the applicant never mentions any detector.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain import AiUsePolicy
from app.pipeline import ai_use as detector
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.store.base import Store, one

ACTIONS = {"no_action": "No action needed", "note": "Private note added", "ask": "Asked the applicant to explain in their own words"}
LABELS = {"academic_achievements": "Academic achievements", "leadership": "Leadership", "community_engagement": "Community engagement",
          "nt_contribution": "How will studying in the NT contribute to your future and the Northern Territory?"}
_FIELD = re.compile(r"^([a-z][a-z0-9_]*): ", re.M)


def policy(store: Store, app: dict[str, Any]) -> AiUsePolicy:
    pack = one(store.select("rule_packs", eq={"id": app.get("rule_pack_id")}, limit=1)) if app.get("rule_pack_id") else None
    return AiUsePolicy.model_validate((pack or {}).get("letter_config") or {})


def demo_preview() -> bool:
    """Demo mode only: show the check on the synthetic demo applications as a marked preview, although the gate is not passed."""
    from app.config import get_settings

    return bool(detector.load_config().get("demo_preview")) and get_settings().app_mode == "demo"


def active(store: Store, app: dict[str, Any]) -> tuple[bool, bool]:
    """(shown and runnable, is a demo preview). Real use needs the policy AND the passed gate."""
    pol = policy(store, app)
    if pol.enabled:
        return True, False
    if pol.ai_use_policy and demo_preview():
        return True, True
    return False, False


def fields_from(redacted_text: str) -> dict[str, str]:
    """Each "field_name: value" block of the REDACTED application text (a value may run over several lines)."""
    marks = list(_FIELD.finditer(redacted_text or ""))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(redacted_text)
        out[m.group(1)] = redacted_text[m.end():end].strip()
    return out


def latest_check(store: Store, application_id: str) -> dict[str, Any] | None:
    rows = store.select("ai_use_checks", eq={"application_id": application_id}, order="created_at", desc=True, limit=1)
    return rows[0] if rows else None


def _latest_actions(store: Store, check_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for a in sorted(store.select("ai_use_actions", eq={"check_id": check_id}), key=lambda a: a.get("created_at") or ""):
        out[a["field"]] = a
    return out


def open_count(store: Store, app: dict[str, Any]) -> dict[str, int]:
    """For the 3.3 pill: fields with a Weak or Stronger signal that the officer has not acted on yet. Never blocks anything."""
    check = latest_check(store, app["id"])
    if not check:
        return {"open": 0, "total": 0}
    acted = _latest_actions(store, check["id"])
    flagged = [f for f in check["fields"] if f["result"] in ("Weak signal", "Stronger signal")]
    return {"open": len([f for f in flagged if f["field"] not in acted]), "total": len(check["fields"])}


def run_check(store: Store, actor: Actor, application_id: str, *, scorer: Any = None, count_tokens: Any = None,
              info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the check now (officer only, on request). `scorer`/`count_tokens` are for tests; by default the local model."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    from app.services import outcome_flow

    outcome_flow.require_assessable(store, application_id, "ai_use")   # not run once the outcome is ineligible
    pol = policy(store, app)
    on, preview = active(store, app)
    if not on:
        raise Conflict("The AI-use check is not switched on for this grant (it has not passed its fairness test).")
    if not app.get("redacted_text"):
        raise Conflict("Approve the redaction check first. The check reads redacted text only.")
    cfg = detector.load_config()
    if scorer is None:
        try:
            scorer, count_tokens, info = detector.local_detector(cfg)
        except detector.DetectorUnavailable as exc:
            raise Conflict(str(exc)) from exc
    info = info or {"model": cfg.get("model"), "revision": cfg.get("revision")}
    redacted = fields_from(app["redacted_text"])
    wanted = {f: redacted.get(f, "") for f in pol.ai_check_fields.fields}
    results = detector.check_fields(wanted, scorer=scorer, count_tokens=count_tokens, thresholds=cfg["thresholds"],
                                    min_words=pol.ai_check_fields.min_words, window_tokens=cfg.get("window_tokens", 400))
    row = store.insert("ai_use_checks", {
        "application_id": application_id, "model": info["model"], "revision": info["revision"], "thresholds": cfg["thresholds"],
        "min_words": pol.ai_check_fields.min_words, "fields": [r.to_dict() for r in results], "run_by": actor.user_id,
    })[0]
    tally: dict[str, int] = {}
    for r in results:
        tally[r.result] = tally.get(r.result, 0) + 1
    # Ids, counts and the model only. Never the text, never a score.
    write_audit(store, actor, "ai_use_check.run", application_id=application_id,
                details={"check_id": row["id"], "model": info["model"], "revision": info["revision"], "fields": len(results), "results": tally,
                         "demo_preview": preview})
    return view(store, app)


def record_action(store: Store, actor: Actor, application_id: str, field: str, action: str, note: str | None = None) -> dict[str, Any]:
    """The officer's action for one field. "ask" drafts a neutral request through the evidence-request flow."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    check = latest_check(store, application_id)
    if not check:
        raise NotFound("Run the AI-use check first")
    if field not in {f["field"] for f in check["fields"]}:
        raise NotFound("That field was not checked")
    if action not in ACTIONS:
        raise ValidationFailed("Choose no action, a private note or asking the applicant")
    if action == "note" and not (note or "").strip():
        raise ValidationFailed("Write the note")
    request_id = None
    draft = None
    if action == "ask":
        draft = _draft_request(store, actor, app, field)
        request_id = draft["id"]
    store.insert("ai_use_actions", {"application_id": application_id, "check_id": check["id"], "field": field, "action": action,
                                    "note": (note or "").strip() or None, "request_id": request_id, "officer_id": actor.user_id})
    write_audit(store, actor, "ai_use_check.action", application_id=application_id,
                details={"check_id": check["id"], "field": field, "action": action, "request_id": request_id})
    return {"action": action, "draft": draft, "check": view(store, app)}


def _draft_request(store: Store, actor: Actor, app: dict[str, Any], field: str) -> dict[str, Any]:
    """A neutral request: no mention of any detector, AI or suspicion. The officer edits and approves it before anything is sent."""
    fields = ((app.get("application_text") or {}).get("fields") or {})
    name = (fields.get("applicant_name") or "").split()
    label = LABELS.get(field, field.replace("_", " ").capitalize())
    text = "\n".join([
        f"Hello {name[0] if name else 'applicant'},", "",
        "Thank you for your application. We would like to hear a little more from you.", "",
        f"Please tell us about your answer to \"{label}\" again, in your own words. A few sentences is enough.",
        "You can write plainly. Spelling and grammar do not matter.",
        "If you would rather talk to a person, reply and ask for a phone call.", "", "Study NT Grants Team",
    ])
    row = store.insert("clarification_requests", {
        "application_id": app["id"], "message_text": text, "created_by": actor.user_id, "kind": "evidence",
        "items": [{"field": field, "label": label, "ask": "Explain this answer in your own words.", "why": "To understand the answer better."}],
    })[0]
    write_audit(store, actor, "evidence_request.drafted", application_id=app["id"],
                details={"request_id": row["id"], "fields": [field], "sent": False})
    return row


def view(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    """What sub-step 3.3 shows: the policy, the acknowledgement, and the latest result per field (no percentages)."""
    pol = policy(store, app)
    check = latest_check(store, app["id"])
    answers = {**(((app.get("application_text") or {}).get("fields")) or {}), **(((app.get("application_text") or {}).get("answers")) or {})}
    on, preview = active(store, app)
    out: dict[str, Any] = {
        "enabled": on, "preview": preview, "policy": pol.ai_use_policy,
        "acknowledged": bool(app.get("ai_use_acknowledged")), "acknowledged_at": app.get("ai_use_acknowledged_at"),
        "fields": [], "model": None, "revision": None, "thresholds": None, "checked_at": None, "min_words": pol.ai_check_fields.min_words,
    }
    if not check:
        out["fields"] = [{"field": f, "label": LABELS.get(f, f), "text": answers.get(f) or "", "words": detector.word_count(answers.get(f) or ""),
                          "result": None, "action": None} for f in pol.ai_check_fields.fields]
        return out
    acted = _latest_actions(store, check["id"])
    out.update({"model": check["model"], "revision": check["revision"], "thresholds": check["thresholds"], "checked_at": check.get("created_at")})
    out["fields"] = [{
        "field": f["field"], "label": LABELS.get(f["field"], f["field"]), "text": answers.get(f["field"]) or "", "words": f["words"],
        "result": f["result"],
        "action": ({"action": acted[f["field"]]["action"], "label": ACTIONS[acted[f["field"]]["action"]], "note": acted[f["field"]].get("note"),
                    "request_id": acted[f["field"]].get("request_id")} if f["field"] in acted else None),
    } for f in check["fields"]]
    return out
