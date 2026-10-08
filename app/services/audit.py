"""Audit log writer. Append-only (the database rejects UPDATE/DELETE).

`details` must hold identifiers, counts and codes only - never application
text or personal data.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.services.access import Actor, application_for_staff, require_role
from app.store.base import Store


def write_audit(
    store: Store,
    actor: Actor,
    action: str,
    *,
    application_id: str | None = None,
    rule_id: str | None = None,
    ai_suggestion: str | None = None,
    officer_decision: str | None = None,
    overridden: bool = False,
    reason: str | None = None,
    rule_pack_version: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return store.insert(
        "audit_log",
        {
            "actor_id": actor.user_id,
            "actor_role": actor.role,
            "action": action,
            "application_id": application_id,
            "rule_id": rule_id,
            "ai_suggestion": ai_suggestion,
            "officer_decision": officer_decision,
            "overridden": overridden,
            "reason": reason,
            "rule_pack_version": rule_pack_version,
            "details": details or {},
        },
    )[0]


def enrich(store: Store, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add display names for the officer, rule and application (read-only view data)."""
    from app.services.queue import reference

    names: dict[str, str | None] = {}
    rules: dict[str, dict[str, Any] | None] = {}
    out = []
    for r in rows:
        aid, rid = r.get("actor_id"), r.get("rule_id")
        if aid and aid not in names:
            p = store.select("profiles", eq={"id": aid}, limit=1)
            names[aid] = p[0].get("display_name") if p else None
        if rid and rid not in rules:
            found = store.select("rules", eq={"id": rid}, limit=1)
            rules[rid] = found[0] if found else None
        rule = rules.get(rid) if rid else None
        out.append(
            r
            | {
                "actor_name": names.get(aid) if aid else "System",
                "rule_code": rule["rule_code"] if rule else None,
                "rule_text": rule["rule_text"] if rule else None,
                "application_reference": reference(r["application_id"]) if r.get("application_id") else None,
            }
        )
    return out


AUDIT_COLUMNS = [
    "occurred_at", "actor_name", "actor_role", "action", "application_reference", "rule_code", "ai_suggestion",
    "officer_decision", "overridden", "reason", "rule_pack_version", "details",
]


def list_audit(
    store: Store,
    actor: Actor,
    *,
    application_id: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    require_role(actor, "officer", "admin")
    eq: dict[str, Any] = {}
    if application_id:
        application_for_staff(store, actor, application_id)
        eq["application_id"] = application_id
    if action:
        eq["action"] = action
    if actor_id:
        eq["actor_id"] = actor_id
    rows = store.select("audit_log", eq=eq, order="occurred_at", desc=True, limit=None if since or until else limit)
    if since:
        rows = [r for r in rows if str(r["occurred_at"]) >= since]
    if until:
        rows = [r for r in rows if str(r["occurred_at"]) <= until]
    if actor.role == "officer":
        # Mirror RLS: officers see rows for applications in their organisation.
        visible: dict[str, bool] = {}
        out = []
        for r in rows:
            app_id = r.get("application_id")
            if not app_id:
                continue
            if app_id not in visible:
                try:
                    application_for_staff(store, actor, app_id)
                    visible[app_id] = True
                except Exception:
                    visible[app_id] = False
            if visible[app_id]:
                out.append(r)
        rows = out
    return rows[:limit]


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=AUDIT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in AUDIT_COLUMNS})
    return buf.getvalue()
