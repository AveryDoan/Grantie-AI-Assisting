"""Step 2: Redaction check.

The AI cannot read an application until the officer approves this step. The officer sees what was redacted, grouped by type, can
look at every item with its surrounding text (the original value stays hidden unless they ask to reveal it, and each reveal is
audit-logged), can add a missed item or unmask an item that is not personal (with a reason), and approves. If the leak scan
failed, the step cannot be approved. Approval runs the AI pipeline on the redacted version only.

Values the officer adds or unmasks are stored encrypted (same key as the token map). The audit log records counts, ids and types,
never the values.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.services.redaction_service import APPLICATION_SOURCE, _cipher, load_edits, run_and_store
from app.store.base import Store, one

# What the officer can add, grouped. Each option makes a token of its own type, so the redacted text reads naturally ([PASSPORT_1]).
OPTIONS: list[dict[str, str]] = [
    {"group": "Identity", "key": "full_name", "label": "Full name", "token": "PERSON"},
    {"group": "Identity", "key": "first_name", "label": "First name only", "token": "PERSON"},
    {"group": "Identity", "key": "family_name", "label": "Family name only", "token": "PERSON"},
    {"group": "Identity", "key": "nickname", "label": "Preferred name or nickname", "token": "PERSON"},
    {"group": "Identity", "key": "referee_name", "label": "Referee name", "token": "REFEREE"},
    {"group": "Identity", "key": "family_member", "label": "Family member name", "token": "FAMILY"},
    {"group": "Identity", "key": "signature", "label": "Signature or initials", "token": "SIGNATURE"},
    {"group": "Contact", "key": "email", "label": "Email address", "token": "EMAIL"},
    {"group": "Contact", "key": "phone", "label": "Phone number", "token": "PHONE"},
    {"group": "Contact", "key": "social", "label": "Social media handle or profile URL", "token": "HANDLE"},
    {"group": "Contact", "key": "website", "label": "Website that identifies the person", "token": "URL"},
    {"group": "Location", "key": "street", "label": "Street address", "token": "ADDRESS"},
    {"group": "Location", "key": "suburb", "label": "Suburb or town", "token": "SUBURB"},
    {"group": "Location", "key": "postcode", "label": "Postcode", "token": "POSTCODE"},
    {"group": "Location", "key": "site", "label": "Specific school or workplace location (not the provider)", "token": "SITE"},
    {"group": "Location", "key": "country", "label": "Country (only if the grant rules don't need it)", "token": "COUNTRY"},
    {"group": "Identifiers", "key": "dob", "label": "Date of birth", "token": "DOB"},
    {"group": "Identifiers", "key": "passport", "label": "Passport number", "token": "PASSPORT"},
    {"group": "Identifiers", "key": "student_id", "label": "Student ID", "token": "STUDENT_ID"},
    {"group": "Identifiers", "key": "visa_no", "label": "Visa or grant number", "token": "VISA_NO"},
    {"group": "Identifiers", "key": "tfn", "label": "Tax file number", "token": "TFN"},
    {"group": "Identifiers", "key": "licence", "label": "Driver licence", "token": "LICENCE"},
    {"group": "Identifiers", "key": "medicare", "label": "Medicare number", "token": "MEDICARE"},
    {"group": "Identifiers", "key": "bank", "label": "Bank account or BSB", "token": "BANK"},
    {"group": "Identifiers", "key": "other_ref", "label": "Application reference number from another system", "token": "REF_NO"},
    {"group": "Identifiers", "key": "other_id", "label": "Other ID number", "token": "ID"},
    {"group": "Organisations", "key": "employer", "label": "Employer name", "token": "EMPLOYER"},
    {"group": "Organisations", "key": "club", "label": "Organisation or club name (when it identifies the person)", "token": "ORG"},
    {"group": "Organisations", "key": "referee_org", "label": "Referee's organisation", "token": "REFEREE_ORG"},
    {"group": "Sensitive", "key": "health", "label": "Health information", "token": "HEALTH"},
    {"group": "Sensitive", "key": "disability", "label": "Disability", "token": "DISABILITY"},
    {"group": "Sensitive", "key": "religion", "label": "Religion", "token": "RELIGION"},
    {"group": "Sensitive", "key": "ethnicity", "label": "Ethnicity or cultural background", "token": "ETHNICITY"},
    {"group": "Sensitive", "key": "hardship", "label": "Family or personal hardship detail", "token": "HARDSHIP"},
    {"group": "Sensitive", "key": "legal", "label": "Legal matter", "token": "LEGAL"},
    {"group": "Other", "key": "other", "label": "Other personal information (describe it in one line)", "token": "OTHER_INFO"},
]
BY_KEY = {o["key"]: o for o in OPTIONS}
_GROUP_CATEGORY = {"Identity": "Name", "Contact": "Email", "Location": "Address", "Identifiers": "ID number", "Organisations": "Organisation",
                   "Sensitive": "Sensitive", "Other": "Other"}
CATEGORY = {"PERSON": "Name", "REFEREE": "Name", "EMAIL": "Email", "PHONE": "Phone", "ADDRESS": "Address", "ID": "ID number", "DOB": "Date of birth",
            **{o["token"]: _GROUP_CATEGORY[o["group"]] for o in OPTIONS if o["token"] not in ("PERSON", "REFEREE", "EMAIL", "PHONE", "ADDRESS", "ID", "DOB")}}
CATEGORY.update({"PLACE": "Location", "HANDLE": "Email", "URL": "Email", "SUBURB": "Address", "POSTCODE": "Address", "SITE": "Address", "COUNTRY": "Address"})
ORDER = ["Name", "Email", "Phone", "Address", "ID number", "Date of birth", "Organisation", "Sensitive", "Location", "Other"]
# The older, shorter list still works (tests and old callers).
ADD_TYPES = {"Name": "PERSON", "Email": "EMAIL", "Phone": "PHONE", "Address": "ADDRESS", "ID number": "ID", "Date of birth": "DOB"}
# Rule inputs: the provider, the course, visa subclass, key dates and the country class. Hiding one can stop a check from working.
PROTECTED_FIELDS = ("education_provider", "course_name", "visa_subclass", "course_start_date", "arrival_date", "study_load", "country", "country_of_residence")
CONTEXT = 48


def _latest_run(store: Store, application_id: str) -> dict[str, Any] | None:
    return one(store.select("redaction_runs", eq={"application_id": application_id}, order="started_at", desc=True, limit=1))


def leak_summary(run: dict[str, Any] | None) -> dict[str, Any]:
    checks = dict((run or {}).get("leak_scan") or {})
    found = sum(int(v) for v in checks.values())
    return {"passed": run is not None and found == 0 and (run.get("status") not in ("running", "blocked_redaction_leak")),
            "found": found, "checks": checks}


def approval_blockers(store: Store, app: dict[str, Any]) -> list[str]:
    """What stops the officer approving, in plain words (empty = they may approve)."""
    run = _latest_run(store, app["id"])
    if run is None or run.get("status") == "running":
        return ["Redaction has not run yet"]
    out = []
    if not leak_summary(run)["passed"]:
        out.append("The leak scan failed. Resolve it (add a missed item or run redaction again) before approving")
    if app.get("ai_status") not in ("ready",) and leak_summary(run)["passed"]:
        out.append("Redaction is held for review, so the AI cannot read this application yet")
    return out


def _texts(store: Store, app: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """source key -> (redacted text, label)."""
    out = {APPLICATION_SOURCE: (app.get("redacted_text") or "", "Application form")}
    for d in store.select("documents", eq={"application_id": app["id"]}):
        if d.get("redacted_text") and not d.get("superseded"):
            out[f"document:{d['id']}"] = (d["redacted_text"], f"{(d.get('declared_type') or 'document').replace('_', ' ').capitalize()} · {d['file_name']}")
    return out


def _field_at(text: str, pos: int) -> str | None:
    start = text.rfind("\n", 0, pos) + 1
    m = re.match(r"([a-z][a-z0-9_]*): ", text[start:pos + 1] if start < pos else text[start:start + 60])
    return m.group(1) if m else None


def _items(store: Store, app: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    from redaction.storage import load_token_map

    tokens = load_token_map(store, app["id"], _cipher(settings))
    texts = _texts(store, app)
    out = []
    for source, occs in tokens.occurrences.items():
        text, label = texts.get(source, ("", source))
        for o in occs:
            entry = tokens.entries[o.token]
            kind = "Location" if o.token.startswith("[LOCATION") else CATEGORY.get(entry.token_type, "Other")
            field = _field_at(text, o.red_start) if source == APPLICATION_SOURCE else None
            out.append({
                "id": f"{source}|{o.red_start}", "type": kind, "token": o.token, "source": source,
                "where": f"{label}{' · ' + field.replace('_', ' ') if field else ''}",
                "before": text[max(0, o.red_start - CONTEXT):o.red_start], "after": text[o.red_end:o.red_end + CONTEXT],
                "_original": o.original,
            })
    return out


def overview(store: Store, actor: Actor, application_id: str, settings: Settings) -> dict[str, Any]:
    """The grouped table. Original values are never in it."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    run = _latest_run(store, application_id)
    groups: list[dict[str, Any]] = []
    if run and run.get("status") != "running":
        items = _items(store, app, settings)
        for kind in ORDER:
            mine = [i for i in items if i["type"] == kind]
            if not mine:
                continue
            tokens = sorted({i["token"] for i in mine})
            groups.append({"type": kind, "count": len(mine),
                           "how": tokens[:3] + ([f"+{len(tokens) - 3} more"] if len(tokens) > 3 else []),
                           "where": sorted({i["where"] for i in mine})})
    rows = store.select("redaction_edits", eq={"application_id": application_id})
    per_source: dict[str, int] = {}
    for it in (_items(store, app, settings) if run and run.get("status") != "running" else []):
        per_source[it["source"]] = per_source.get(it["source"], 0) + 1
    documents = [{"id": d["id"], "file_name": d["file_name"], "declared_type": d.get("declared_type"),
                  "is_pdf": str(d["file_name"]).lower().endswith(".pdf"), "redacted_spans": per_source.get(f"document:{d['id']}", 0)}
                 for d in sorted(store.select("documents", eq={"application_id": application_id}), key=lambda d: d["file_name"]) if not d.get("superseded")]
    return {
        "documents": documents,
        "run": None if not run else {k: run.get(k) for k in ("id", "status", "started_at", "finished_at", "counts", "detector_version")},
        "ai_status": app.get("ai_status", "not_redacted"), "leak_scan": leak_summary(run), "groups": groups,
        "blockers": approval_blockers(store, app), "edits": {"added": sum(r["kind"] == "add" for r in rows), "unmasked": sum(r["kind"] == "unmask" for r in rows)},
        "texts": {k: {"label": v[1], "redacted": v[0]} for k, v in _texts(store, app).items()},
    }


def group_items(store: Store, actor: Actor, application_id: str, kind: str, settings: Settings) -> list[dict[str, Any]]:
    """Every item of one type with the redacted text around it. The original is not included."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in _items(store, app, settings) if i["type"] == kind]


def reveal(store: Store, actor: Actor, application_id: str, item_id: str, settings: Settings) -> dict[str, Any]:
    """Show one original value, on request. The reveal is audit-logged (who, when, which item and type, never the value)."""
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    item = next((i for i in _items(store, app, settings) if i["id"] == item_id), None)
    if item is None:
        raise NotFound("That item no longer exists. Reload the list.")
    write_audit(store, actor, "redaction.reveal", application_id=application_id,
                details={"item_id": item_id, "type": item["type"], "token": item["token"]})
    return {"item_id": item_id, "original": item["_original"]}


def _rerun(store: Store, actor: Actor, app: dict[str, Any], settings: Settings) -> None:
    from app.services import steps

    run_and_store(store, actor, app["id"], settings, force=True)
    steps.touch(store, actor, store.select("applications", eq={"id": app["id"]})[0], 2, settings)


def _protected(app: dict[str, Any], text: str) -> list[str]:
    """Typed rule inputs that this selection overlaps. A warning, not a block: the officer decides."""
    f = _fields_of(app)
    t = text.strip().casefold()
    return [k.replace("_", " ") for k in PROTECTED_FIELDS if str(f.get(k) or "").strip() and len(t) >= 3
            and (t in str(f[k]).casefold() or str(f[k]).casefold() in t)]


def _fields_of(app: dict[str, Any]) -> dict[str, Any]:
    return (app.get("application_text") or {}).get("fields") or {}


def _resolve_kind(kind: str, description: str | None) -> tuple[str, str]:
    """(token type, label for the audit log). Accepts an option key, an option label or one of the older short names."""
    if kind in BY_KEY:
        o = BY_KEY[kind]
    elif kind in ADD_TYPES:
        return ADD_TYPES[kind], kind
    else:
        o = next((x for x in OPTIONS if x["label"] == kind), None)
    if o is None:
        raise ValidationFailed("Choose what kind of detail this is")
    if o["key"] == "other" and not (description or "").strip():
        raise ValidationFailed("Describe the personal information in one line")
    return o["token"], o["key"]


def preview_missed(store: Store, actor: Actor, application_id: str, source: str, text: str, kind: str, match_case: bool,
                   every: bool, settings: Settings) -> dict[str, Any]:
    """Before confirming: how many places change, what the placeholder will look like, and whether a rule needs this value."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    token, _ = _resolve_kind(kind, "x")
    text = text.strip()
    texts = _texts(store, app)
    flags = 0 if match_case else re.IGNORECASE
    pat = re.compile(re.escape(text), flags)
    here = len(pat.findall(texts.get(source, ("", ""))[0]))
    total = sum(len(pat.findall(v[0])) for v in texts.values())
    from redaction.storage import load_token_map

    n = _load_counter(store, app, settings, token)
    protected = _protected(app, text) + (["country"] if token == "COUNTRY" else [])
    return {"matches": total if every else min(here, 1), "in_this_text": here, "token_preview": f"[{token}_{n + 1}]", "protected": sorted(set(protected)),
            "warning": "The rules need this value. Hiding it may stop a check from working." if protected else None}


def _load_counter(store: Store, app: dict[str, Any], settings: Settings, token: str) -> int:
    from redaction.storage import load_token_map

    tokens = load_token_map(store, app["id"], _cipher(settings))
    return int(getattr(tokens, "_counters", {}).get(token, 0))


def add_missed(store: Store, actor: Actor, application_id: str, source: str, text: str, kind: str, settings: Settings,
               description: str | None = None) -> dict[str, Any]:
    """The officer selected text that was not redacted. It is stored encrypted and redaction (and the leak scan) runs again.
    Every matching occurrence in this application is replaced."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    texts = _texts(store, app)
    text = text.strip()
    token, label = _resolve_kind(kind, description)
    if source not in texts or len(text) < 2 or len(text) > 200:
        raise ValidationFailed("Select a short passage in the redacted text")
    if text not in texts[source][0] or re.fullmatch(r"\[[A-Z_]+_\d+\]", text):
        raise ValidationFailed("Select text that is visible in the redacted version and is not already a placeholder")
    cipher = _cipher(settings)
    store.insert("redaction_edits", {"application_id": application_id, "kind": "add", "token_type": token, "source": source,
                                     "encrypted_value": cipher.encrypt(application_id, "edit:add", text), "officer_id": actor.user_id})
    write_audit(store, actor, "redaction.added", application_id=application_id,
                details={"source": source, "type": label, "token_type": token, "characters": len(text), "added_by_officer": True,
                         "protected_warning": bool(_protected(app, text)), "described": bool((description or "").strip())})
    _rerun(store, actor, app, settings)
    return overview(store, actor, application_id, settings)


def unmask(store: Store, actor: Actor, application_id: str, item_id: str, reason: str | None, settings: Settings) -> dict[str, Any]:
    """The officer says an item is not personal. It needs a reason. A known personal value (the applicant's own name, for example)
    is redacted regardless, and the officer is told."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailed("Say why this is not personal")
    item = next((i for i in _items(store, app, settings) if i["id"] == item_id), None)
    if item is None:
        raise NotFound("That item no longer exists. Reload the list.")
    cipher = _cipher(settings)
    edit = store.insert("redaction_edits", {"application_id": application_id, "kind": "unmask", "token_type": "OTHER", "source": item["source"],
                                            "encrypted_value": cipher.encrypt(application_id, "edit:unmask", item["_original"]),
                                            "reason": reason, "officer_id": actor.user_id})[0]
    run_and_store(store, actor, application_id, settings, force=True)
    still = any(i["_original"].casefold() == item["_original"].casefold() for i in _items(store, store.select("applications", eq={"id": application_id})[0], settings))
    if still:   # a known personal value: take the edit back
        store.delete("redaction_edits", eq={"id": edit["id"]})
        run_and_store(store, actor, application_id, settings, force=True)
        raise ValidationFailed("This is a known personal detail from the application, so it stays redacted.")
    write_audit(store, actor, "redaction.unmasked", application_id=application_id, reason=reason,
                details={"item_id": item_id, "type": item["type"], "token": item["token"]})
    from app.services import steps

    steps.touch(store, actor, store.select("applications", eq={"id": application_id})[0], 2, settings)
    return overview(store, actor, application_id, settings)


def compare(store: Store, actor: Actor, application_id: str, source: str, reveal_original: bool, settings: Settings) -> dict[str, Any]:
    """Original and redacted text side by side. The redacted side is exactly what the AI sees.

    The original is masked on the SERVER until the officer switches it on (one audit entry per switch-on, never the text), so
    a blurred original is never sent to the browser. Spans carry their type and the offsets in both texts."""
    from redaction.restore import restore
    from redaction.storage import load_token_map

    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    texts = _texts(store, app)
    if source not in texts:
        raise NotFound("That text does not exist")
    redacted, label = texts[source]
    tokens = load_token_map(store, application_id, _cipher(settings))
    original = restore(redacted, tokens, source)
    spans = []
    shift = 0   # the original is the redacted text with each token swapped for its value, so offsets follow from the redacted ones
    for o in sorted(tokens.occurrences.get(source, []), key=lambda o: o.red_start):
        entry = tokens.entries[o.token]
        kind = "Location" if o.token.startswith("[LOCATION") else CATEGORY.get(entry.token_type, "Other")
        start = o.red_start + shift
        spans.append({"id": f"{source}|{o.red_start}", "type": kind, "token": o.token, "red": [o.red_start, o.red_end], "orig": [start, start + len(o.original)]})
        shift += len(o.original) - (o.red_end - o.red_start)
    if reveal_original:
        write_audit(store, actor, "redaction.compare_original_opened", application_id=application_id, details={"source": source})
        shown = original
    else:   # same line breaks and length of words, nothing readable
        shown = re.sub(r"[^\W_]", "\u2022", original)
    return {"source": source, "label": label, "redacted": redacted, "original": shown, "original_shown": reveal_original, "spans": spans,
            "has_text": bool(redacted.strip()),
            "types": sorted({sp["type"] for sp in spans})}


def approve(store: Store, actor: Actor, application_id: str, settings: Settings, llm: Any) -> dict[str, Any]:
    """Approve the redaction check, then run the AI on the redacted version only. Opens step 3."""
    from app.pipeline.orchestrator import run_assessment
    from app.services import steps

    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    if app["status"] == "signed_off":
        raise Conflict("This application has been signed off")
    steps.require_unlocked(store, app, 2, settings)
    blockers = approval_blockers(store, app)
    if blockers:
        raise Conflict("The redaction check cannot be approved yet", details={"blockers": blockers})
    run = _latest_run(store, application_id)
    steps.set_step(store, actor, app, 2, "done", notice=None, detail={"redaction_run_id": run["id"]})
    write_audit(store, actor, "redaction.approved", application_id=application_id,
                details={"redaction_run_id": run["id"], "counts": run.get("counts"), "leak_scan": run.get("leak_scan")})
    try:
        result = run_assessment(store, actor, application_id, llm, settings)
    except Exception:
        steps.set_step(store, actor, app, 2, "in_progress", reason="the AI check could not run")
        raise
    return {"run": result, "steps": steps.state(store, store.select("applications", eq={"id": application_id})[0], settings)}
