"""Consistency flags as a service: run, persist, review, count, link.

- Flags are persisted with a deterministic key, so running again keeps the same flag (and the officer's decision).
- An officer confirms or dismisses every flag. A dismissal needs a note. Both are audited (ids and codes only).
- A flag never changes a rule result and never blocks sign-off: nothing in review or sign-off reads this table.
- The queue only shows "N flags to check". Weak signals alone never make an application show a count.
- Identifiers are hashed (keyed) before they are stored. Quotes are stored redacted and restored for officers only.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from app.config import Settings
from app.logging_utils import get_logger
from app.pipeline.consistency import ConsistencyContext, cross_application, run_consistency
from app.pipeline.consistency.common import reference
from app.pipeline.consistency.context import build_docs
from app.pipeline.consistency.models import CHECK_TYPES, TYPE_LABEL, Flag
from app.pipeline.parsing import parse_date
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.store.base import Store, StoreError, now_iso, one

log = get_logger(__name__)

ATTRIBUTE_LABEL = {
    "cross_application.shared_contact_email": "Shared contact email address",
    "cross_application.shared_contact_phone": "Shared contact phone number",
    "cross_application.shared_contact_address": "Shared address",
    "cross_application.shared_referee_name": "Same referee",
    "cross_application.shared_referee_email": "Shared referee email address",
    "cross_application.shared_referee_phone": "Shared referee phone number",
    "cross_application.shared_referee_email_domain": "Shared referee email domain",
    "cross_application.identical_text": "Almost identical document text",
    "cross_application.reused_wording": "Largely the same wording",
}


def enabled(settings: Settings) -> bool:
    return bool(settings.consistency_layer)


# ---------------------------------------------------------------- running


def build_context(store: Store, app: dict[str, Any], documents: list[dict[str, Any]], outcome: Any, pack: Any,
                  guard: Any, settings: Settings) -> ConsistencyContext:
    """The context for one application, from a finished assessment outcome (redaction first, always)."""
    text = app.get("application_text") or {}
    redacted = {d.document_id: d.redacted_text for d in outcome.redaction.documents if d.included_in_ai_input and d.redacted_text}
    detected = {c.document_id: c.detected_type for c in outcome.document_checks.checks}
    originals = {d["id"]: (d.get("extracted_text") or "") for d in documents}
    token = None
    m = re.search(r"(?m)^applicant_name:\s*(\[[A-Z]+_\d+\])", outcome.redaction.redacted_text)
    if m:
        token = m.group(1)
    submitted = parse_date((app.get("submitted_at") or "")[:10]) or date.today()
    key = settings.identifier_hash_key.get_secret_value()
    return ConsistencyContext(
        app_id=app["id"], fields={k: str(v) for k, v in (text.get("fields") or {}).items()},
        answers={k: str(v) for k, v in (text.get("answers") or {}).items()}, submitted=submitted,
        docs=build_docs(documents, detected, redacted, originals), referees=list(outcome.referees),
        form_text=outcome.redaction.redacted_text, applicant_token=token, token_map=outcome.redaction.token_map,
        guard=guard, store=store, applicant_id=app.get("applicant_id"), hash_key=key or None,
        letters_need_marks=any((r.params or {}).get("check") == "referee_fields" for r in pack.rules),
        fuzzy_threshold=settings.quote_fuzzy_threshold, name_threshold=settings.name_match_threshold,
    )


def _row(app_id: str, run_id: str | None, f: Flag) -> dict[str, Any]:
    return {"application_id": app_id, "run_id": run_id, "flag_key": f.key, "check_id": f.check_id, "check_type": f.type,
            "strength": f.strength, "description": f.description, "evidence": [e.model_dump(exclude_none=True) for e in f.evidence],
            "verification": f.verification}


def persist_flags(store: Store, app_id: str, run_id: str | None, flags: list[Flag], replace_types: set[str]) -> dict[str, int]:
    """Upsert by key. An open flag that is no longer raised is removed; one an officer has decided on is kept."""
    existing = {r["flag_key"]: r for r in store.select("consistency_flags", eq={"application_id": app_id})}
    now = {f.key for f in flags}
    removed = 0
    for key, r in existing.items():
        if r["check_type"] in replace_types and r["status"] == "open" and key not in now:
            store.delete("consistency_flags", eq={"id": r["id"]})
            removed += 1
    added = 0
    for f in flags:
        row = _row(app_id, run_id, f)
        if f.key in existing:
            store.update("consistency_flags", {k: v for k, v in row.items() if k not in ("application_id", "flag_key")},
                         eq={"id": existing[f.key]["id"]})
        else:
            store.insert("consistency_flags", row)
            added += 1
    return {"added": added, "removed": removed}


def run_for_assessment(store: Store, actor: Actor, app: dict[str, Any], run: dict[str, Any], outcome: Any,
                       documents: list[dict[str, Any]], pack: Any, llm: Any, settings: Settings) -> dict[str, Any]:
    """Run every check for one application and store the flags, the hashes and the trace. Never raises."""
    summary: dict[str, Any] = {}
    try:
        from redaction.pipeline import GuardedLLM

        guard = GuardedLLM(llm, outcome.redaction) if llm is not None else None
        ctx = build_context(store, app, documents, outcome, pack, guard, settings)
        result = run_consistency(ctx)
        trace = dict(result.trace)

        # The pool check: hash this application's identifiers and wording, then refresh every linked application.
        previously_linked = cross_application.linked_apps(store, app["id"])
        idents, fingers = cross_application.collect_identifiers(ctx), cross_application.collect_fingerprints(ctx)
        if ctx.hash_key:
            cross_application.save(store, app["id"], idents, fingers)
        group = {app["id"]} | previously_linked | cross_application.linked_apps(store, app["id"])
        pool_counts = {"identifiers": len(idents), "fingerprints": len(fingers), "linked_applications": len(group) - 1}
        for aid in group:
            persist_flags(store, aid, None, cross_application.flags_for(store, aid), {"cross_application"})
        trace["cross_application"] = pool_counts if ctx.hash_key else {"skipped": "IDENTIFIER_HASH_KEY is not set"}
        mine = [f for f in result.flags]
        persist_flags(store, app["id"], run["id"], mine, {"cross_document", "timeline", "document_integrity", "narrative"})
        store.update("assessment_runs", {"consistency_trace": trace}, eq={"id": run["id"]})
        flags_now = store.select("consistency_flags", eq={"application_id": app["id"]})
        summary = {
            "flags": len(flags_now), "strong": sum(r["strength"] == "strong" and r["verification"] == "verified" for r in flags_now),
            "weak": sum(r["strength"] == "weak" and r["verification"] == "verified" for r in flags_now),
            "unclear": sum(r["verification"] == "unclear" for r in flags_now),
            "dropped_unverified": sum((trace.get(k) or {}).get("dropped_unverified", 0) for k in ("timeline", "narrative")),
        }
        write_audit(store, actor, "consistency.checked", application_id=app["id"], details={"run_id": run["id"], **summary})
    except Exception as exc:
        log.warning("consistency layer failed for an application: %s", type(exc).__name__)
        summary = {"error": type(exc).__name__}
    return summary


# ---------------------------------------------------------------- reading


def _restored(store: Store, app: dict[str, Any], settings: Settings, rows: list[dict[str, Any]], restorer: Any = None) -> list[dict[str, Any]]:
    from app.services.redaction_service import QuoteRestorer

    restorer = restorer or QuoteRestorer(store, app, settings)
    out = []
    for r in rows:
        evidence = []
        for e in r.get("evidence") or []:
            e = dict(e)
            if e.get("kind") == "quote":
                src = e.get("source")
                e["restored"] = restorer.original(e.get("quote"), src if src and src != "form" else "application_text")
            evidence.append(e)
        out.append({**r, "evidence": evidence, "type_label": TYPE_LABEL.get(r["check_type"], r["check_type"])})
    order = {"strong": 0, "weak": 1}
    out.sort(key=lambda r: (r["verification"] == "unclear", CHECK_TYPES.index(r["check_type"]), order[r["strength"]], r["check_id"]))
    return out


def application_flags(store: Store, app: dict[str, Any], settings: Settings, restorer: Any = None) -> dict[str, Any]:
    """The officer's view of one application's flags (quotes restored to the applicant's words), and the overview rows
    ("Consistency of information": every comparison, including the ones that agree)."""
    if not enabled(settings):
        return {"enabled": False, "flags": [], "trace": {}}
    try:
        rows = store.select("consistency_flags", eq={"application_id": app["id"]})
        runs = store.select("assessment_runs", eq={"application_id": app["id"], "status": "complete"}, order="finished_at", desc=True, limit=1)
    except StoreError:
        log.warning("consistency tables are not available (has migration 0011 been applied?)")
        return {"enabled": False, "flags": [], "trace": {}, "unavailable": True}
    from app.services import consistency_overview
    from app.services.redaction_service import QuoteRestorer

    restorer = restorer or QuoteRestorer(store, app, settings)
    flags = _restored(store, app, settings, rows, restorer)
    trace = (runs[0].get("consistency_trace") if runs else None) or {}
    overview = consistency_overview.build(app, [d for d in store.select("documents", eq={"application_id": app["id"]}) if not d.get("superseded")], flags, trace,
                                          restorer, restorer.source_texts()) if runs else []
    return {"enabled": True, "flags": flags, "trace": trace, "overview": overview}


def flags_to_check(store: Store, app_ids: list[str], settings: Settings) -> dict[str, int]:
    """For the queue: how many open flags to check, shown ONLY when at least one open flag is strong."""
    if not (enabled(settings) and app_ids):
        return {}
    try:
        rows = store.select("consistency_flags", in_={"application_id": app_ids})
    except StoreError:
        return {}
    by_app: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["status"] == "open" and r["verification"] == "verified":
            by_app.setdefault(r["application_id"], []).append(r)
    return {a: len(rs) for a, rs in by_app.items() if any(r["strength"] == "strong" for r in rs)}


# ---------------------------------------------------------------- the officer's decision


def review_flag(store: Store, actor: Actor, flag_id: str, action: str, note: str | None, settings: Settings) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    row = one(store.select("consistency_flags", eq={"id": flag_id}, limit=1))
    if not row:
        raise NotFound("Flag not found")
    application_for_staff(store, actor, row["application_id"])   # same access rule as everything else
    if action not in ("confirm", "dismiss"):
        raise ValidationFailed("Choose confirm or dismiss")
    note = (note or "").strip()
    if action == "dismiss" and len(note) < 3:
        raise ValidationFailed("A note is required to dismiss a flag")
    status = "confirmed" if action == "confirm" else "dismissed"
    updated = store.update("consistency_flags", {"status": status, "note": note or None, "reviewed_by": actor.user_id,
                                                 "reviewed_at": now_iso()}, eq={"id": flag_id})[0]
    write_audit(store, actor, f"consistency.flag_{status}", application_id=row["application_id"], reason=note or None,
                details={"flag_id": flag_id, "check_id": row["check_id"], "strength": row["strength"], "type": row["check_type"]})
    return updated


# ---------------------------------------------------------------- the pool view


def linked_groups(store: Store, actor: Actor, settings: Settings) -> list[dict[str, Any]]:
    """Groups of applications linked by a shared attribute. Names the attribute, never its value."""
    require_role(actor, "officer", "admin")
    if not enabled(settings):
        return []
    try:
        rows = [r for r in store.select("consistency_flags") if r["check_type"] == "cross_application"]
    except StoreError:
        return []
    from app.services.access import staff_can_access_program

    apps = {a["id"]: a for a in store.select("applications")}
    allowed = {i for i, a in apps.items() if staff_can_access_program(store, actor, a["grant_program_id"])}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges: list[tuple[str, str, dict[str, Any]]] = []
    for r in rows:
        if r["application_id"] not in allowed:
            continue
        for e in r.get("evidence") or []:
            other = (e.get("source") or "").removeprefix("application:") if e.get("kind") == "link" else None
            if other and other in allowed:
                edges.append((r["application_id"], other, r))
                parent[find(r["application_id"])] = find(other)
    groups: dict[str, dict[str, Any]] = {}
    for a, b, r in edges:
        g = groups.setdefault(find(a), {"members": set(), "attrs": {}, "open": 0})
        g["members"] |= {a, b}
        attr = g["attrs"].setdefault(r["check_id"], {"check_id": r["check_id"], "label": ATTRIBUTE_LABEL.get(r["check_id"], r["check_id"]),
                                                     "strength": r["strength"]})
        if r["strength"] == "strong":
            attr["strength"] = "strong"
    names = {a["id"]: ((a.get("application_text") or {}).get("fields") or {}).get("applicant_name") for a in apps.values()}
    for r in rows:
        if r["application_id"] in allowed and r["status"] == "open":
            for g in groups.values():
                if r["application_id"] in g["members"]:
                    g["open"] += 1
                    break
    out = []
    for g in groups.values():
        members = sorted(g["members"], key=reference)
        out.append({
            "id": reference(members[0]) + "+" + str(len(members)),
            "applications": [{"id": m, "reference": reference(m), "applicant_name": names.get(m)} for m in members],
            "attributes": sorted(g["attrs"].values(), key=lambda x: (x["strength"] != "strong", x["label"])),
            "open_flags": g["open"],
        })
    out.sort(key=lambda g: -len(g["applications"]))
    return out
