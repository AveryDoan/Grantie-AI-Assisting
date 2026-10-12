"""Outcomes that end with a letter.

A. Ineligible. After an officer confirms a rule as Not met, the officer may go to Outcome now. The checks the decline does not
   need (the rest of eligibility, consistency, the AI-use check, merit) are recorded as "Not assessed (ineligible)". Only the
   officer's click does this: no AI or code path calls go_to_outcome.
B. Not selected (competitive). People decide who is funded, outside this tool. The officer records that decision for each
   application (who decided, under what authority, where it is minuted, a reason note). Nothing here orders, compares or
   totals applications: the list is by reference, and only eligible applications with complete merit can be decided.

Sign-off and release: one deliberate action per application ("Sign off and release letter"), only when the letter was read to
the end and every check passes. A batch release only releases letters that are signed off and that this officer opened, after
the officer types the count. Every step is in the audit log.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.config import Settings
from app.services import letters
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, now_iso, one

NOT_ASSESSED = "Not assessed (ineligible)"
SKIPPABLE = ("eligibility", "consistency", "ai_use", "merit")
REASON_MIN = 20


# ------------------------------------------------------------------ reading the state

def competitive(letter_config: dict[str, Any]) -> bool:
    """A competitive round: eligible applications wait for the people's round decision before their letter."""
    return bool(letter_config.get("competitive_round"))


def pack_config(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    pack = one(store.select("rule_packs", eq={"id": app.get("rule_pack_id")}, limit=1)) if app.get("rule_pack_id") else None
    return (pack or {}).get("letter_config") or {}


def _eligibility(store: Store, application_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(eligibility findings of the latest run, their latest reviews, rules by id). Merit criteria are left out."""
    run = latest_run(store, application_id)
    if run is None:
        return [], {}, {}
    rules = {r["id"]: r for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})}
    findings = [f for f in store.select("findings", eq={"run_id": run["id"]})
                if (rules.get(f["rule_id"], {}).get("params") or {}).get("section") != "merit"]
    return findings, latest_reviews(store, [f["id"] for f in findings]), rules


def confirmed_not_met(store: Store, application_id: str) -> list[dict[str, Any]]:
    findings, reviews, _ = _eligibility(store, application_id)
    return [f for f in findings if f["id"] in reviews and reviews[f["id"]]["action"] in ("confirm", "override")
            and reviews[f["id"]]["final_status"] == "Not met"]


def skipped(store: Store, application_id: str) -> list[dict[str, Any]]:
    return [r for r in store.select("steps_not_assessed", eq={"application_id": application_id}) if not r.get("cleared_at")]


def on_ineligible_path(store: Store, application_id: str) -> bool:
    return bool(skipped(store, application_id)) and bool(confirmed_not_met(store, application_id))


def skipped_steps(store: Store, application_id: str) -> set[str]:
    return {r["step"] for r in skipped(store, application_id)} if on_ineligible_path(store, application_id) else set()


def latest_decision(store: Store, application_id: str) -> dict[str, Any] | None:
    return max(store.select("round_decisions", eq={"application_id": application_id}), key=lambda d: d["version"], default=None)


def merit_status(store: Store, app: dict[str, Any]) -> tuple[bool, list[str]]:
    """(complete, plain reasons it is not). Complete = each criterion marked with a reason, or Not assessed with a reason."""
    run = latest_run(store, app["id"])
    if run is None:
        return False, ["The AI check has not run"]
    codes = sorted({r["rule_code"] for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})
                    if (r.get("params") or {}).get("section") == "merit"})
    marks = {m["rule_code"]: m for m in store.select("merit_marks", eq={"application_id": app["id"]})}
    missing = [c for c in codes if c not in marks]
    no_reason = [c for c in codes if c in marks and not (marks[c].get("reason") or "").strip()]
    out = ([f"{', '.join(missing)} not marked yet"] if missing else []) + \
          ([f"{', '.join(no_reason)} set to Not assessed without a reason"] if no_reason else [])
    return not out, out


def all_met(store: Store, application_id: str) -> bool:
    findings, reviews, _ = _eligibility(store, application_id)
    return bool(findings) and all(f["id"] in reviews and reviews[f["id"]]["action"] != "ask_applicant"
                                  and reviews[f["id"]]["final_status"] == "Met" for f in findings)


def refresh_eligibility(store: Store, app: dict[str, Any]) -> str | None:
    """Set applications.eligibility_result from officer decisions only: ineligible after a confirmed Not met, eligible when every
    eligibility rule is decided Met, otherwise not set. Called after each officer decision."""
    result = "ineligible" if confirmed_not_met(store, app["id"]) else "eligible" if all_met(store, app["id"]) else None
    if app.get("eligibility_result") != result and app["status"] != "signed_off":
        store.update("applications", {"eligibility_result": result}, eq={"id": app["id"]})
    return result


# ------------------------------------------------------------------ A. ineligible path

def could_change(store: Store, application_id: str) -> list[str]:
    """Eligibility rules still Needs evidence or Unclear (the officer's decision, or the AI's suggestion if undecided)."""
    findings, reviews, rules = _eligibility(store, application_id)
    out = []
    for f in findings:
        rv = reviews.get(f["id"])
        status = "Needs evidence" if rv and rv["action"] == "ask_applicant" else (rv["final_status"] if rv else f["ai_status"])
        if status in ("Needs evidence", "Unclear"):
            out.append(rules[f["rule_id"]]["rule_code"])
    return sorted(out)


def go_to_outcome(store: Store, actor: Actor, application_id: str, *, continue_anyway: bool = False,
                  settings: Settings | None = None) -> dict[str, Any]:
    """The officer's own click: "Go to Outcome now". Records which checks are not needed for the decline, with who and when."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    not_met = confirmed_not_met(store, application_id)
    if not not_met:
        raise Conflict("Confirm a rule as Not met first. Only an officer's decision can end the assessment early.")
    if on_ineligible_path(store, application_id):
        return {"application_id": application_id, "not_assessed": sorted(skipped_steps(store, application_id))}
    open_rules = could_change(store, application_id)
    if open_rules and not continue_anyway:
        n = len(open_rules)
        raise Conflict(f"{n} {'finding' if n == 1 else 'findings'} could change this if the applicant sent more information. Ask for it first?",
                       details={"could_change": open_rules})
    from app.services import substeps

    findings, reviews, rules = _eligibility(store, application_id)
    undecided = [rules[f["rule_id"]]["rule_code"] for f in findings if f["id"] not in reviews or reviews[f["id"]]["action"] == "ask_applicant"]
    steps_out = (["eligibility"] if undecided else []) + ["consistency"] + (["ai_use"] if substeps.ai_use_enabled(store, app) else []) + ["merit"]
    for step in steps_out:
        store.insert("steps_not_assessed", {"application_id": application_id, "step": step, "reason": NOT_ASSESSED,
                                            "officer_id": actor.user_id, "created_at": now_iso()})
    refresh_eligibility(store, app)
    codes = sorted(rules[f["rule_id"]]["rule_code"] for f in not_met)
    write_audit(store, actor, "outcome.go_now", application_id=application_id,
                details={"not_met_rules": codes, "not_assessed": steps_out, "eligibility_rules_not_checked": sorted(undecided),
                         "could_change_acknowledged": open_rules})
    letters.redraft(store, actor, application_id, settings)
    return {"application_id": application_id, "not_assessed": steps_out, "not_met_rules": codes}


def after_decision(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> None:
    """After any officer decision on a finding: update the eligibility result, clear the ineligible path if no Not met is left,
    and keep the decline letter in step. Never moves an application anywhere by itself."""
    app = one(store.select("applications", eq={"id": application_id}, limit=1))
    if not app or app["status"] == "signed_off":
        return
    rows = skipped(store, application_id)
    if rows and not confirmed_not_met(store, application_id):
        for r in rows:
            store.update("steps_not_assessed", {"cleared_at": now_iso(), "cleared_by": actor.user_id}, eq={"id": r["id"]})
        write_audit(store, actor, "outcome.path_cleared", application_id=application_id,
                    details={"steps_needed_again": sorted(r["step"] for r in rows)})
    refresh_eligibility(store, app)
    letters.redraft(store, actor, application_id, settings)


def require_assessable(store: Store, application_id: str, what: str) -> None:
    """Merit and the AI-use check are not run for an application on the ineligible path (whether or not they were shown)."""
    if what in ("merit", "ai_use") and skipped_steps(store, application_id):
        raise Conflict(f"{NOT_ASSESSED}: this application ends at eligibility, so this check is not run")


# ------------------------------------------------------------------ B. round decisions

def readiness(store: Store, app: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if app["status"] == "signed_off":
        reasons.append("Signed off")
    if not all_met(store, app["id"]):
        reasons.append("Not every eligibility rule is confirmed Met by an officer")
    ok, why = merit_status(store, app)
    reasons += why
    return not reasons, reasons


def round_list(store: Store, actor: Actor, settings: Settings | None = None) -> dict[str, Any]:
    """Applications in competitive rounds: ready for a decision, already decided, or not ready yet (with why). By reference only."""
    from app.services import queue

    if not actor.is_staff:
        raise NotFound("Not found")
    rows = queue.list_queue(store, actor, settings)
    apps = {a["id"]: a for a in store.select("applications", in_={"id": [r["id"] for r in rows]})} if rows else {}
    ready, decided, not_ready = [], [], []
    for r in sorted(rows, key=lambda x: x["reference"]):
        app = apps[r["id"]]
        if not competitive(pack_config(store, app)) or app.get("eligibility_result") == "ineligible":
            continue
        item = {"application_id": r["id"], "reference": r["reference"], "program_name": r["program_name"],
                "deep_link": f"#/applications/{r['id']}?step=4"}
        dec = latest_decision(store, r["id"])
        if dec:
            decided.append(item | {"decision": dec["decision"], "decided_at": dec["decided_at"], "reference_minute": dec["reference"],
                                   "signed_off": app["status"] == "signed_off"})
            continue
        ok, why = readiness(store, app)
        (ready if ok else not_ready).append(item if ok else item | {"why": why, "deep_link": f"#/applications/{r['id']}?step=3"})
    return {"ready": ready, "decided": decided, "not_ready": not_ready,
            "note": "No ranking is shown here. People decide who is funded; this screen only records their decision."}


def record_decisions(store: Store, actor: Actor, application_ids: list[str], *, decision: str, decided_by: str, authority: str,
                     decided_at: str, reference: str, reason_note: str | None, typed_count: int | None,
                     settings: Settings | None = None) -> list[dict[str, Any]]:
    """Record the people's decision for the chosen applications. Each one is checked; nothing is released here: Not selected
    gets a draft letter (no feedback until the officer ticks some), Selected gets the next-steps draft."""
    from app.services import scores

    require_role(actor, "officer")
    scores.require(store, actor, scores.RECORD)     # can_record_round_decisions (the panel and the lead officer by default)
    ids = list(dict.fromkeys(application_ids))
    if not ids:
        raise ValidationFailed("Choose at least one application")
    if decision not in ("selected", "not_selected_competitive"):
        raise ValidationFailed("decision must be selected or not_selected_competitive")
    decided_by, authority, reference = (decided_by or "").strip(), (authority or "").strip(), (reference or "").strip()
    note = (reason_note or "").strip() or None
    if not decided_by or not authority:
        raise ValidationFailed("Say who made the decision and under what authority")
    if not reference:
        raise ValidationFailed("Give the reference to where the decision was made (panel minute or meeting)")
    try:
        date.fromisoformat(decided_at)
    except (TypeError, ValueError):
        raise ValidationFailed("decided_at must be a date (YYYY-MM-DD)") from None
    if decision == "not_selected_competitive" and len(note or "") < REASON_MIN:
        raise ValidationFailed(f"A Not selected decision needs a reason note of at least {REASON_MIN} characters")
    if len(ids) > 1 and typed_count != len(ids):
        raise ValidationFailed(f"Type the number of applications ({len(ids)}) to confirm this decision")
    apps = []
    for aid in ids:
        app = application_for_staff(store, actor, aid)
        if not competitive(pack_config(store, app)):
            raise Conflict(f"{aid}: this grant round does not use round decisions")
        if app.get("eligibility_result") != "eligible":
            refresh_eligibility(store, app)
            app = one(store.select("applications", eq={"id": aid}, limit=1))
        ok, why = readiness(store, app)
        if not ok:
            raise Conflict("Not every application is ready for a round decision", details={"application_id": aid, "why": why})
        apps.append(app)
    out = []
    for app in apps:
        version = (latest_decision(store, app["id"]) or {}).get("version", 0) + 1
        row = store.insert("round_decisions", {
            "application_id": app["id"], "version": version, "decision": decision, "decided_by": decided_by, "authority": authority,
            "decided_at": decided_at, "reference": reference, "reason_note": note, "created_by": actor.user_id, "created_at": now_iso(),
            # the marks, weights, rule pack version and total as they were now: later changes cannot rewrite this decision
            "score_snapshot": scores.snapshot(scores.for_application(store, app), now_iso()),
        })[0]
        write_audit(store, actor, "round.decision", application_id=app["id"],
                    details={"decision": decision, "version": version, "batch_size": len(apps), "reference": reference,
                             "score_snapshot": row.get("score_snapshot")})
        try:
            if decision == "not_selected_competitive":
                letters.generate_not_selected(store, actor, app["id"], [], settings)
            else:
                letters.generate_next_steps(store, actor, app["id"], settings)
        except Conflict:
            pass   # the officer drafts it from Outcome
        out.append(row)
    return out


# ------------------------------------------------------------------ the letter an application needs, sign-off and release

def letter_kind(store: Store, app: dict[str, Any]) -> str | None:
    if confirmed_not_met(store, app["id"]):
        return "decline"
    if not all_met(store, app["id"]):
        return None
    if competitive(pack_config(store, app)):
        dec = latest_decision(store, app["id"])
        return None if not dec else "next_steps" if dec["decision"] == "selected" else "not_selected"
    return "next_steps"


def outcome_status_for(store: Store, app: dict[str, Any]) -> str:
    kind = letter_kind(store, app)
    if kind == "decline":
        return "ineligible"
    dec = latest_decision(store, app["id"])
    if dec:
        return dec["decision"]
    if all_met(store, app["id"]):
        return "eligible_awaiting_round_decision" if competitive(pack_config(store, app)) else "in_progress"
    return "in_progress"


def sign_off_conditions(store: Store, actor: Actor, app: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """What the Outcome screen shows as a checklist, and whether "Sign off and release letter" is allowed."""
    from app.services import review

    kind = letter_kind(store, app)
    letter = letters.latest_of(store, app["id"], kind) if kind else None
    _, pending = review.undecided_rules(store, app["id"])
    checks = letters.recheck(store, letter, letter["body_text"], settings) if letter else None
    items = [
        {"key": "decided", "label": "Every rule this outcome needs has your decision", "ok": not pending},
        {"key": "decision", "label": {"decline": "At least one rule confirmed Not met by you",
                                      "not_selected": "The round decision Not selected is recorded",
                                      "next_steps": "Every eligibility rule is confirmed Met"}.get(kind or "", "The outcome is known"),
         "ok": kind is not None},
        {"key": "letter", "label": "The letter is drafted", "ok": letter is not None},
        {"key": "viewed", "label": "You read the letter to the end", "ok": bool(letter and letter.get("letter_viewed_at"))},
        {"key": "placeholders", "label": "No [placeholder] is left in the letter", "ok": bool(checks and not checks["placeholders"])},
        {"key": "checks", "label": "Every letter check passes", "ok": bool(checks and checks["all_passed"])},
        {"key": "current", "label": "The letter matches your current decisions", "ok": bool(letter and letters.matches_findings(store, letter, settings))},
    ]
    blocker = next((i for i in items if not i["ok"]), None)
    plain = {"viewed": "Review the letter first", "placeholders": "Fill in the placeholders in the letter",
             "checks": "Fix the letter so every check passes", "current": "Redraft the letter: your decisions changed",
             "letter": "Draft the letter first", "decided": "Decide every rule first", "decision": "The outcome is not decided yet"}
    return {"kind": kind, "letter_id": letter["id"] if letter else None, "items": items, "ready": blocker is None,
            "reason": plain[blocker["key"]] if blocker else None, "checks": checks}


def sign_off_and_release(store: Store, actor: Actor, application_id: str, *, statement_acknowledged: bool, release_now: bool = True,
                         settings: Settings | None = None) -> dict[str, Any]:
    """One deliberate action: sign off the application and release its letter (or hold it for a batch release)."""
    from app.services import review

    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    cond = sign_off_conditions(store, actor, app, settings)
    if not cond["ready"]:
        raise Conflict(cond["reason"], details={"items": cond["items"]})
    letter = one(store.select("letters", eq={"id": cond["letter_id"]}, limit=1))
    status = outcome_status_for(store, app)
    review.sign_off(store, actor, application_id, statement_acknowledged=statement_acknowledged)
    store.update("applications", {"outcome_status": status, "eligibility_result": "ineligible" if status == "ineligible" else "eligible"},
                 eq={"id": application_id})
    now = now_iso()
    letter = store.update("letters", {"signed_off_by": actor.user_id, "signed_off_at": now, "checks_passed": True}, eq={"id": letter["id"]})[0]
    write_audit(store, actor, "outcome.signed_off", application_id=application_id,
                details={"outcome_status": status, "letter_id": letter["id"], "letter_kind": letter.get("kind"), "release_now": release_now})
    if release_now:
        letter = letters.release(store, actor, letter, settings)
    return {"application_id": application_id, "outcome_status": status, "letter": letter}


def release_many(store: Store, actor: Actor, letter_ids: list[str], *, typed_count: int | None,
                 settings: Settings | None = None) -> list[dict[str, Any]]:
    """Release several signed-off letters, each of which THIS officer opened. The officer types the count. No select-all."""
    require_role(actor, "officer")
    ids = list(dict.fromkeys(letter_ids))
    if not ids:
        raise ValidationFailed("Choose the letters to release")
    if typed_count != len(ids):
        raise ValidationFailed(f"Type the number of letters ({len(ids)}) to confirm the release")
    rows = []
    for lid in ids:
        letter = one(store.select("letters", eq={"id": lid}, limit=1))
        if not letter:
            raise NotFound("Letter not found")
        application_for_staff(store, actor, letter["application_id"])
        if not letter.get("signed_off_at"):
            raise Conflict("Only letters that are signed off can be released", details={"letter_id": lid})
        if not letter.get("letter_viewed_at") or letter.get("viewed_by") != actor.user_id:
            raise Conflict("Open and read each letter yourself before releasing it", details={"letter_id": lid})
        rows.append(letter)
    return [letters.release(store, actor, l, settings) for l in rows]


def held_letters(store: Store, actor: Actor) -> list[dict[str, Any]]:
    """Signed-off letters that are not released yet (by reference). For the batch release."""
    from app.services import queue

    out = []
    for r in queue.list_queue(store, actor):
        for l in store.select("letters", eq={"application_id": r["id"]}):
            if l.get("signed_off_at") and not l.get("released_at"):
                out.append({"letter_id": l["id"], "application_id": r["id"], "reference": r["reference"], "kind": l.get("kind"),
                            "viewed_by_me": l.get("viewed_by") == actor.user_id and bool(l.get("letter_viewed_at"))})
    return sorted(out, key=lambda x: x["reference"])
