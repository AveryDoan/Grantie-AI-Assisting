"""Merit marks: the officer's own 0 to 100 mark for a merit criterion.

- The mark is the officer's. Nothing here (and no AI step) suggests, pre-fills or estimates it.
- A mark needs a reason. A criterion can instead be set to "Not assessed".
- Every change is audited with who, when, and the old and new value.
- No total, average, weighted score, rank or comparison is calculated anywhere: this module only reads and writes one
  criterion at a time. The merit weights on the rules stay reference-only.
- Sign-off is blocked until every merit criterion is marked or set to "Not assessed" (see services.review.undecided_rules).
"""

from __future__ import annotations

from typing import Any

from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.store.base import Store, now_iso, one

MAX_REASON = 2000


def merit_codes(store: Store, application: dict[str, Any]) -> list[str]:
    """Rule codes of the merit criteria in this application's rule pack."""
    rules = store.select("rules", eq={"rule_pack_id": application["rule_pack_id"]})
    return sorted((r["rule_code"] for r in rules if (r.get("params") or {}).get("section") == "merit"),
                  key=lambda c: (len(c), c))


def marks_for(store: Store, application_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """application id -> rule code -> mark row."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not application_ids:
        return out
    for row in store.select("merit_marks", in_={"application_id": application_ids}):
        out.setdefault(row["application_id"], {})[row["rule_code"]] = row
    return out


def public(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {k: row.get(k) for k in ("mark", "not_assessed", "reason", "updated_at")}


def set_mark(store: Store, actor: Actor, application_id: str, rule_code: str, *, mark: int | None, not_assessed: bool,
             reason: str | None) -> dict[str, Any]:
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off; marks can no longer be changed")
    if rule_code not in merit_codes(store, app):
        raise NotFound("That merit criterion does not exist for this application")
    reason = (reason or "").strip()
    if len(reason) > MAX_REASON:
        raise ValidationFailed(f"The reason is too long (most {MAX_REASON} characters)")
    if not_assessed:
        if mark is not None:
            raise ValidationFailed("Choose a mark or Not assessed, not both")
        reason = reason or ""
    else:
        if mark is None or isinstance(mark, bool) or not isinstance(mark, int) or not 0 <= mark <= 100:
            raise ValidationFailed("The mark must be a whole number from 0 to 100")
        if not reason:
            raise ValidationFailed("A mark needs a reason")

    old = one(store.select("merit_marks", eq={"application_id": application_id, "rule_code": rule_code}, limit=1))
    values = {"mark": mark, "not_assessed": not_assessed, "reason": reason or None, "officer_id": actor.user_id, "updated_at": now_iso()}
    if (old and (old["mark"], old["not_assessed"], old.get("reason")) == (mark, not_assessed, reason or None)):
        return public(old)   # nothing changed: nothing to record (double click, retry)
    if old:
        row = store.update("merit_marks", values, eq={"id": old["id"]})[0]
    else:
        row = store.insert("merit_marks", {"application_id": application_id, "rule_code": rule_code, **values})[0]
    write_audit(
        store, actor, "merit.mark", application_id=application_id, reason=reason or None,
        details={"rule_code": rule_code,
                 "old_mark": old["mark"] if old else None, "new_mark": mark,
                 "old_not_assessed": bool(old["not_assessed"]) if old else False, "new_not_assessed": not_assessed},
    )
    return public(row)
