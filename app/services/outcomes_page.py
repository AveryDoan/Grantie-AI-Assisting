"""Data for the Outcomes page: scores and decisions, ineligible, letters to release, released.

The weighted total appears only here, only for people with can_view_round_scores. Rows are by reference (the default order);
any other order is the viewer's click in the browser. Applicant names are hidden unless asked for, and asking is logged. There is
no rank, no cut-off and no label on a total. Exports use references, and every export is logged.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.config import Settings
from app.services import letters, outcome_flow, queue, scores
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, ValidationFailed
from app.store.base import Store, one


def _plain(d: Any) -> str:
    """40, not 4E+1."""
    from decimal import Decimal

    return f"{Decimal(d).normalize():f}"


def decision_stands(store: Store, application_id: str) -> bool:
    """A round decision is in force unless an officer has since started a reconsideration (a reason, logged)."""
    dec = outcome_flow.latest_decision(store, application_id)
    if not dec:
        return False
    after = [a for a in store.select("audit_log", eq={"application_id": application_id, "action": "round.reconsideration"})
             if (a.get("created_at") or "") > (dec.get("created_at") or "")]
    return not after


def _eligible_apps(store: Store, actor: Actor, settings: Settings | None) -> list[dict[str, Any]]:
    rows = queue.list_queue(store, actor, settings)
    apps = {a["id"]: a for a in store.select("applications", in_={"id": [r["id"] for r in rows]})} if rows else {}
    out = []
    for r in sorted(rows, key=lambda x: x["reference"]):
        app = apps[r["id"]]
        if outcome_flow.competitive(outcome_flow.pack_config(store, app)) and outcome_flow.all_met(store, app["id"]) \
                and not outcome_flow.on_ineligible_path(store, app["id"]):
            out.append(app | {"_reference": r["reference"], "_name": r.get("applicant_name")})
    return out


def table(store: Store, actor: Actor, settings: Settings | None = None, *, names: bool = False) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    scores.require(store, actor, scores.VIEW)
    apps = _eligible_apps(store, actor, settings)
    if names:
        write_audit(store, actor, "scores.names_shown", application_id=None, details={"applications": len(apps)})
    pack_id = max({a["rule_pack_id"] for a in apps}, key=lambda p: sum(a["rule_pack_id"] == p for a in apps), default=None)
    apps = [a for a in apps if a["rule_pack_id"] == pack_id]
    crit = scores.criteria(store, pack_id) if pack_id else {"items": [], "error": "No eligible applications yet", "weight_sum": 0, "normalised": False}
    pack = one(store.select("rule_packs", eq={"id": pack_id}, limit=1)) if pack_id else {}
    cfg = (pack or {}).get("letter_config") or {}
    rows = []
    for a in apps:
        res = scores.for_application(store, a)
        dec = outcome_flow.latest_decision(store, a["id"]) if decision_stands(store, a["id"]) else None
        ready = res["status"] == "complete" and dec is None and a["status"] != "signed_off"
        rows.append({
            "application_id": a["id"], "reference": a["_reference"], "name": a["_name"] if names else None,
            "marks": {r["code"]: ("NA" if r["not_assessed"] else r["mark"]) for r in res["rows"]},
            "total": scores.show(res["total"], res["decimals"]), "total_exact": None if res["total"] is None else str(res["total"]),
            "status": res["status"], "reason": res["reason"], "marked": res["marked"], "of": res["of"],
            "decision": dec["decision"] if dec else None, "ready": ready,
            "why_not_ready": None if ready else ("A decision is recorded" if dec else "Signed off" if a["status"] == "signed_off" else res["reason"] or "Marks are not finished"),
            "deep_link": f"#/applications/{a['id']}?step=3&sub=merit",
        })
    return {
        "criteria": [{"code": c["code"], "title": c["title"], "weight": None if c["weight"] is None else _plain(c["weight"])} for c in crit["items"]],
        "weights_error": crit["error"], "normalised": crit["normalised"], "weight_sum": _plain(crit["weight_sum"]),
        "policy": scores.policy_of(cfg), "decimals": int(cfg.get("score_decimals", 1)),
        "places_available": cfg.get("places_available"), "names_shown": names, "rows": rows,
        "can_record": scores.has_permission(store, actor, scores.RECORD),
        "formula": "weighted total = sum(mark × weight) ÷ sum(weights)",
    }


def breakdown(store: Store, actor: Actor, application_id: str, settings: Settings | None = None, *, names: bool = False) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    scores.require(store, actor, scores.VIEW)
    app = application_for_staff(store, actor, application_id)
    res = scores.for_application(store, app)
    marks = {m["rule_code"]: m for m in store.select("merit_marks", eq={"application_id": application_id})}
    profiles = {p["id"]: p.get("display_name") or "Officer" for p in store.select("profiles")}
    history = [{"rule_code": (a.get("details") or {}).get("rule_code"), "old_mark": (a.get("details") or {}).get("old_mark"),
                "new_mark": (a.get("details") or {}).get("new_mark"), "not_assessed": (a.get("details") or {}).get("new_not_assessed"),
                "reason": a.get("reason"), "by": profiles.get(a.get("actor_id"), "Officer"), "at": a.get("created_at")}
               for a in sorted(store.select("audit_log", eq={"application_id": application_id, "action": "merit.mark"}),
                               key=lambda a: a.get("created_at") or "", reverse=True)]
    dec = outcome_flow.latest_decision(store, application_id)
    ref = queue.reference(application_id)
    return {
        "application_id": application_id, "reference": ref, "name": (queue._names(store, app)["applicant_name"] if names else None),
        "status": res["status"], "reason": res["reason"], "total": scores.show(res["total"], res["decimals"]), "marked": res["marked"], "of": res["of"],
        "policy": res["policy"], "worked_example": scores.worked_example(res | {"decimals": res["decimals"]}),
        "rows": [{"code": r["code"], "title": r["title"], "weight": None if r["weight"] is None else _plain(r["weight"]), "mark": r["mark"], "not_assessed": r["not_assessed"],
                  "points": None if r["points"] is None or r["weight"] is None else str((r["points"] / 100).quantize(scores.Decimal("0.01"))),
                  "reason": (marks.get(r["code"]) or {}).get("reason"),
                  "by": profiles.get((marks.get(r["code"]) or {}).get("officer_id")), "at": (marks.get(r["code"]) or {}).get("updated_at"),
                  "evidence": f"#/applications/{application_id}?step=3&sub=merit"} for r in res["rows"]],
        "eligibility": "All eligibility rules confirmed Met by an officer" if outcome_flow.all_met(store, application_id) else "Not every eligibility rule is confirmed Met",
        "history": history,
        "decision": None if not dec else {k: dec.get(k) for k in ("decision", "decided_by", "authority", "decided_at", "reference", "reason_note", "version", "score_snapshot")}
                    | {"stands": decision_stands(store, application_id)},
        "can_edit_marks": not decision_stands(store, application_id) and app["status"] != "signed_off",
        "can_record": scores.has_permission(store, actor, scores.RECORD), "deep_link": f"#/applications/{application_id}?step=3&sub=merit",
    }


def reconsider(store: Store, actor: Actor, application_id: str, reason: str) -> dict[str, Any]:
    """Marks cannot change under a recorded decision. A reconsideration (a reason, logged) lifts that, and a new decision is then recorded."""
    require_role(actor, "officer")
    scores.require(store, actor, scores.RECORD)
    application_for_staff(store, actor, application_id)
    if not (reason or "").strip():
        raise ValidationFailed("A reconsideration needs a reason")
    if not decision_stands(store, application_id):
        raise Conflict("There is no recorded decision to reconsider")
    write_audit(store, actor, "round.reconsideration", application_id=application_id, reason=reason.strip())
    return {"application_id": application_id, "reconsidering": True}


def csv_export(store: Store, actor: Actor, settings: Settings | None = None) -> str:
    """For the panel: reference, marks, weights, total, decision. References only (no names). Every export is logged."""
    data = table(store, actor, settings)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Reference", *[f"{c['code']} {c['title']} ({c['weight']}%)" if c["weight"] else f"{c['code']} {c['title']} (not scored)" for c in data["criteria"]], "Weighted total", "Marks status", "Decision"])
    for r in data["rows"]:
        w.writerow([r["reference"], *[r["marks"].get(c["code"], "") if r["marks"].get(c["code"]) is not None else "" for c in data["criteria"]],
                    r["total"] or "Not yet", r["status"], r["decision"] or "Not decided"])
    write_audit(store, actor, "scores.exported", details={"rows": len(data["rows"]), "format": "csv"})
    return out.getvalue()


def lists(store: Store, actor: Actor, settings: Settings | None = None) -> dict[str, Any]:
    """The other tabs: ineligible (with their letters), letters to release, released."""
    require_role(actor, "officer", "admin")
    rows = queue.list_queue(store, actor, settings)
    ineligible, released = [], []
    for r in rows:
        app = one(store.select("applications", eq={"id": r["id"]}, limit=1))
        if outcome_flow.confirmed_not_met(store, r["id"]):
            l = letters.latest_of(store, r["id"], "decline")
            ineligible.append({"application_id": r["id"], "reference": r["reference"], "signed_off": app["status"] == "signed_off",
                               "letter": None if not l else {"status": l["status"], "viewed": bool(l.get("letter_viewed_at")), "released": bool(l.get("released_at"))},
                               "deep_link": f"#/applications/{r['id']}?step=4"})
        for l in store.select("letters", eq={"application_id": r["id"]}):
            if l.get("released_at"):
                released.append({"application_id": r["id"], "reference": r["reference"], "kind": l.get("kind"), "released_at": l["released_at"],
                                 "delivery": "Ready to send (no email connected)", "deep_link": f"#/applications/{r['id']}?step=4"})
    return {"ineligible": sorted(ineligible, key=lambda x: x["reference"]), "released": sorted(released, key=lambda x: (x["reference"], x["released_at"])),
            "held": outcome_flow.held_letters(store, actor)}
