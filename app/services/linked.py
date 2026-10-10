"""Linked applications for one application: other applications that share something with it.

- A shared identifier is shown only as a short masked reference of its keyed hash (never the value, never a name).
- A document match is shown as the kind of document, with no text.
- "Open" is offered only for applications the officer already has access to.
- A link is for the officer to check. It is not a finding against any applicant, and it changes no rule result.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.consistency.cross_application import KIND_STRENGTH
from app.services.access import Actor, staff_can_access_program
from app.services.errors import ValidationFailed  # noqa: F401  (kept for callers that import from here)
from app.store.base import Store, StoreError

SHARED_LABEL = {
    "contact_email": "Contact email address", "contact_phone": "Phone number", "contact_address": "Address",
    "referee_name": "Referee", "referee_email": "Referee's email address", "referee_phone": "Referee's phone number",
    "referee_email_domain": "Referee's email domain",
}
EXACT, SIMILAR = "Exact match", "Similar"
WORDING = {"cross_application.identical_text": ("Document match", EXACT), "cross_application.reused_wording": ("Document match", SIMILAR)}


def mask(hash_value: str) -> str:
    """A short, non-reversible reference: enough to see that two applications share the same item."""
    return f"ref {hash_value[:8]}"


def for_application(store: Store, actor: Actor, app: dict[str, Any], reference: Any) -> list[dict[str, Any]]:
    try:
        mine = store.select("identifier_hashes", eq={"application_id": app["id"]})
        flags = [f for f in store.select("consistency_flags", eq={"application_id": app["id"]}) if f["check_type"] == "cross_application"]
    except StoreError:
        return []
    others: dict[str, list[dict[str, Any]]] = {}
    if mine:
        by_hash = {r["hash"]: r for r in mine}
        for r in store.select("identifier_hashes", in_={"hash": sorted(by_hash)}):
            if r["application_id"] == app["id"]:
                continue
            kind = by_hash[r["hash"]]["kind"]
            strength = SIMILAR if KIND_STRENGTH.get(kind) == "weak" else EXACT
            item = {"what": SHARED_LABEL.get(kind, kind), "ref": mask(r["hash"]), "strength": strength}
            if item not in others.setdefault(r["application_id"], []):
                others[r["application_id"]].append(item)
    for f in flags:
        if f["check_id"] not in WORDING:
            continue
        what, strength = WORDING[f["check_id"]]
        for e in f.get("evidence") or []:
            if e.get("kind") == "link":
                other = (e.get("source") or "").removeprefix("application:")
                label = (e.get("label") or "").split(": ", 1)[-1].split(" is about")[0].strip()
                item = {"what": what, "ref": label or "wording", "strength": strength}
                if other and item not in others.setdefault(other, []):
                    others[other].append(item)
    apps = {a["id"]: a for a in store.select("applications", in_={"id": sorted(others)})} if others else {}
    out = []
    for other_id, items in others.items():
        other = apps.get(other_id)
        if other is None:
            continue
        out.append({
            "application_id": other_id, "reference": reference(other_id), "shared": sorted(items, key=lambda i: (i["strength"] != EXACT, i["what"])),
            "strength": EXACT if any(i["strength"] == EXACT for i in items) else SIMILAR,
            "can_open": staff_can_access_program(store, actor, other["grant_program_id"]),
        })
    out.sort(key=lambda r: (r["strength"] != EXACT, r["reference"]))
    return out
