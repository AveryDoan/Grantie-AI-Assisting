"""Step 3, Assessment, in sub-steps: 3.1 Eligibility, 3.2 Consistency, 3.3 AI-use check (only when the rule pack has an AI-use
policy and the check is enabled), then Merit (3.4, or 3.3 when there is no AI-use check).

This module is the ONE place the sub-step counts come from. The pills, the section cards and the header all read them from
the steps endpoint, so they cannot disagree. Gating:
- Eligibility is complete when every finding has an officer decision.
- Consistency and the AI-use check never block. They show "Reviewed" once the officer has opened them.
- Merit is complete when every criterion is marked or set to Not assessed.
Nothing here decides a rule, a mark or an outcome.
"""

from __future__ import annotations

from typing import Any

from app.services.audit import write_audit
from app.store.base import Store, StoreError, one

ELIGIBILITY, CONSISTENCY, AI_USE, MERIT = "eligibility", "consistency", "ai_use", "merit"
TITLES = {ELIGIBILITY: "Eligibility", CONSISTENCY: "Consistency", AI_USE: "AI-use check", MERIT: "Merit"}
OPTIONAL = {CONSISTENCY, AI_USE}


def pack_config(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    pack = one(store.select("rule_packs", eq={"id": app.get("rule_pack_id")}, limit=1)) if app.get("rule_pack_id") else None
    return (pack or {}).get("letter_config") or {}


def ai_use_enabled(store: Store, app: dict[str, Any]) -> bool:
    """Shown only if the rule pack declares an AI-use policy AND its fairness gate switched the check on
    (or, in demo mode only, as a marked preview on the synthetic demo applications)."""
    from app.services import ai_use

    return ai_use.active(store, app)[0]


def opened(store: Store, application_id: str) -> set[str]:
    row = one(store.select("application_steps", eq={"application_id": application_id, "step": 3}, limit=1))
    return set(((row or {}).get("detail") or {}).get("opened") or [])


def mark_opened(store: Store, actor: Any, app: dict[str, Any], key: str) -> dict[str, Any]:
    """The officer opened an optional sub-step: show its "Reviewed" tick. Logged once per sub-step."""
    if key not in OPTIONAL:
        return {"key": key, "opened": False}
    row = one(store.select("application_steps", eq={"application_id": app["id"], "step": 3}, limit=1))
    seen = set(((row or {}).get("detail") or {}).get("opened") or [])
    if key in seen:
        return {"key": key, "opened": True}
    detail = {**((row or {}).get("detail") or {}), "opened": sorted(seen | {key})}
    if row:
        store.update("application_steps", {"detail": detail}, eq={"id": row["id"]})
    else:
        store.insert("application_steps", {"application_id": app["id"], "step": 3, "status": "in_progress", "detail": detail,
                                           "updated_by": actor.user_id})
    write_audit(store, actor, "substep.opened", application_id=app["id"], details={"substep": key})
    return {"key": key, "opened": True}


def _counts(store: Store, app: dict[str, Any], run: dict[str, Any] | None, pending: list[str]) -> dict[str, Any]:
    from app.services import ai_use

    rules = store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]}) if run else []
    merit_codes = {r["rule_code"] for r in rules if (r.get("params") or {}).get("section") == "merit"}
    rule_codes = {r["rule_code"] for r in rules} - merit_codes
    # Consistency of information only (cross-document, timeline, document metadata, narrative). Linked applications are not counted.
    try:
        rows = store.select("consistency_flags", eq={"application_id": app["id"]})
    except StoreError:  # the consistency tables are not migrated yet: nothing to count
        rows = []
    flags = [r for r in rows if r.get("status") == "open" and r.get("verification") == "verified" and r.get("check_type") != "cross_application"]
    return {
        ELIGIBILITY: {"open": len([c for c in pending if c in rule_codes]), "total": len(rule_codes)},
        MERIT: {"open": len([c for c in pending if c in merit_codes]), "total": len(merit_codes)},
        CONSISTENCY: {"open": len(flags), "total": len(flags)},
        AI_USE: ai_use.open_count(store, app),
    }


def substeps(store: Store, app: dict[str, Any], run: dict[str, Any] | None, pending: list[str], unlocked: bool) -> list[dict[str, Any]]:
    keys = [ELIGIBILITY, CONSISTENCY] + ([AI_USE] if ai_use_enabled(store, app) else []) + [MERIT]
    counts = _counts(store, app, run, pending)
    seen = opened(store, app["id"])
    has_run = run is not None
    out = []
    for n, key in enumerate(keys, start=1):
        c = counts[key]
        if key in OPTIONAL:
            status = "reviewed" if key in seen else "not_started"
            missing = None
        else:
            status = "done" if has_run and c["open"] == 0 else ("in_progress" if has_run else "not_started")
            if not has_run:
                missing = "The AI check has not run yet."
            elif key == ELIGIBILITY:
                missing = f"{c['open']} eligibility {'finding needs' if c['open'] == 1 else 'findings need'} an officer decision." if c["open"] else None
            else:
                missing = f"{c['open']} merit {'criterion needs' if c['open'] == 1 else 'criteria need'} a mark or Not assessed." if c["open"] else None
        out.append({"key": key, "number": f"3.{n}", "title": TITLES[key], "count": c["open"], "total": c["total"],
                    "status": status, "blocking": key not in OPTIONAL, "unlocked": unlocked and (has_run or key == ELIGIBILITY),
                    "missing": missing})
    return out


def progress(substeps_: list[dict[str, Any]]) -> dict[str, int]:
    """The header line: decided out of all items that need an officer decision (eligibility findings and merit criteria)."""
    blocking = [s for s in substeps_ if s["blocking"]]
    total = sum(s["total"] for s in blocking)
    return {"decided": total - sum(s["count"] for s in blocking), "total": total}
