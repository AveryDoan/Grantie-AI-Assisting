"""Officer work queue and application detail views.

The queue is ordered by the officer's open work items (unreviewed findings,
errors, invalid quotes, flags), NOT by any judgement about the applicant.
No view contains an eligibility score or an approve/reject recommendation.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.services.redaction_service import QuoteRestorer
from app.services.access import Actor, application_for_applicant, application_for_staff
from app.services.errors import Forbidden
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, one


def reference(application_id: str) -> str:
    """Short human-readable reference for an application (display only)."""
    return "APP-" + application_id.replace("-", "")[:6].upper()


def _typed_name(app: dict[str, Any]) -> str | None:
    """The name typed on this application (an applicant account can hold several applications)."""
    return ((app.get("application_text") or {}).get("fields") or {}).get("applicant_name")


def _names(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)) or {}
    program = one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}
    return {
        "reference": reference(app["id"]),
        "applicant_name": applicant.get("organisation_name") or _typed_name(app) or applicant.get("display_name"),
        "program_name": program.get("name"),
    }


def _latest_complete(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    complete = [r for r in runs if r["status"] == "complete"]
    return max(complete, key=lambda r: (r.get("finished_at") or "", r.get("started_at") or ""), default=None)


def _compute_attention(
    app: dict[str, Any],
    runs: list[dict[str, Any]],
    docs: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    reviews: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Pure: the officer's open work items for one application."""
    run = _latest_complete(runs)
    items: dict[str, Any] = {
        "not_yet_assessed": run is None and not app.get("manual_assessment_requested"),
        "manual_assessment_requested": bool(app.get("manual_assessment_requested")),
        "failed_runs": sum(r["status"] == "failed" for r in runs),
        "documents_needing_verification": sum(bool(d.get("needs_verification")) for d in docs),
        "unreviewed_findings": 0,
        "invalid_findings": 0,
        "error_findings": 0,
        "language_flags": 0,
        "injection_flags": 0,
        "awaiting_applicant": app["status"] == "awaiting_applicant",
    }
    if run:
        current = [f for f in findings if f["run_id"] == run["id"]]
        items["unreviewed_findings"] = sum(
            1 for f in current if f["id"] not in reviews or reviews[f["id"]]["action"] == "ask_applicant"
        )
        items["invalid_findings"] = sum(not f["is_valid"] for f in current)
        items["error_findings"] = sum(bool(f["error_flag"]) for f in current)
        items["language_flags"] = sum(bool(f["language_flag"]) for f in current)
        items["injection_flags"] = len(run.get("injection_flags") or [])
    return items


def _attention(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    runs = store.select("assessment_runs", eq={"application_id": app["id"]})
    run = _latest_complete(runs)
    findings = store.select("findings", eq={"run_id": run["id"]}) if run else []
    return _compute_attention(
        app, runs, store.select("documents", eq={"application_id": app["id"]}), findings,
        latest_reviews(store, [f["id"] for f in findings]),
    )


def _open_items(a: dict[str, Any]) -> int:
    return sum(int(v) for k, v in a.items() if k != "awaiting_applicant")


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def list_queue(store: Store, actor: Actor, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Batched: a fixed number of queries however many applications there are."""
    if not actor.is_staff:
        raise Forbidden("Officers only")
    apps = [a for a in store.select("applications") if a["status"] != "draft"]
    programs = {p["id"]: p for p in store.select("grant_programs", in_={"id": list({a["grant_program_id"] for a in apps})})}
    if actor.role == "officer":  # same rule as staff_can_access_program, applied in bulk
        apps = [a for a in apps if programs.get(a["grant_program_id"], {}).get("organisation_id") == actor.organisation_id]
    if not apps:
        return []
    ids = [a["id"] for a in apps]
    applicants = {x["id"]: x for x in store.select("applicants", in_={"id": list({a["applicant_id"] for a in apps})})}
    runs = _group(store.select("assessment_runs", in_={"application_id": ids}), "application_id")
    docs = _group(store.select("documents", in_={"application_id": ids}), "application_id")
    latest = {app_id: _latest_complete(rs) for app_id, rs in runs.items()}
    run_ids = [r["id"] for r in latest.values() if r]
    findings = store.select("findings", in_={"run_id": run_ids}) if run_ids else []
    reviews = latest_reviews(store, [f["id"] for f in findings])
    findings_by_app = _group(findings, "application_id")
    from app.services import consistency as consistency_service

    to_check = consistency_service.flags_to_check(store, ids, settings or get_settings())

    out = []
    for app in apps:
        attention = _compute_attention(app, runs.get(app["id"], []), docs.get(app["id"], []),
                                       findings_by_app.get(app["id"], []), reviews)
        applicant = applicants.get(app["applicant_id"], {})
        out.append(
            {
                "id": app["id"],
                "reference": reference(app["id"]),
                "applicant_name": applicant.get("organisation_name") or _typed_name(app) or applicant.get("display_name"),
                "program_name": programs.get(app["grant_program_id"], {}).get("name"),
                "grant_program_id": app["grant_program_id"],
                "status": app["status"],
                "submitted_at": app.get("submitted_at"),
                "attention": attention,
                "open_items": _open_items(attention) if app["status"] != "signed_off" else 0,
                # A count only (never a score). Not part of open_items, so it does not affect the order.
                "flags_to_check": to_check.get(app["id"], 0) if app["status"] != "signed_off" else 0,
            }
        )
    # Most open work first, then oldest submission first. Not a ranking of applicants.
    out.sort(key=lambda r: (r["status"] == "signed_off", -r["open_items"], r["submitted_at"] or ""))
    return out



# Typed form field that matches a field read from a document (the form and documents name them differently).
FORM_KEY_FOR_DOC_FIELD = {"provider_name": "education_provider", "full_name": "applicant_name"}


def rule_sources(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Which form fields (and which document field) a rule reads, so the screen can show where a result came from.

    Real field keys only; the screen turns them into the form's own labels. No rule codes."""
    p = rule.get("params") or {}
    doc_type = p.get("document_type") or ((p.get("required_documents") or [None])[0])
    doc_field = p.get("field") or p.get("date_field")
    typed = [k for k in dict.fromkeys([
        *(p.get("required_fields") or []), p.get("typed_field"), p.get("fact"), p.get("secondary_fact"),
        FORM_KEY_FOR_DOC_FIELD.get(doc_field or ""), doc_field if doc_field in ("course_name", "course_start_date", "study_load") else None,
    ]) if k]
    rows = [{"typed": k, "document_type": None, "document_field": None} for k in typed]
    if doc_type and doc_field and rows:
        match = next((r for r in rows if r["typed"] == FORM_KEY_FOR_DOC_FIELD.get(doc_field, doc_field)), rows[0])
        match["document_type"], match["document_field"] = doc_type, doc_field
    elif doc_type and doc_field:
        rows.append({"typed": None, "document_type": doc_type, "document_field": doc_field})
    return rows


def application_detail(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> dict[str, Any]:
    if actor.role == "applicant":
        return applicant_view(store, actor, application_id)
    from app.services import consistency as consistency_service

    app = application_for_staff(store, actor, application_id)
    run = latest_run(store, application_id)
    restorer = QuoteRestorer(store, app, settings or get_settings())  # officer view: real words, from the encrypted map
    rules = {}
    findings_out = []
    facts = []
    if run:
        rules = {r["id"]: r for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})}
        findings = store.select("findings", eq={"run_id": run["id"]})
        reviews = latest_reviews(store, [f["id"] for f in findings])
        history = store.select("officer_reviews", in_={"finding_id": [f["id"] for f in findings]}) if findings else []
        for f in sorted(findings, key=lambda f: (rules[f["rule_id"]].get("display_order", 0), rules[f["rule_id"]]["rule_code"])):
            rule = rules[f["rule_id"]]
            findings_out.append(
                f
                | {
                    "rule_code": rule["rule_code"],
                    "rule_text": rule["rule_text"],
                    "rule_type": rule["rule_type"],
                    "source_clause": rule.get("source_clause"),
                    # Officer sees the applicant's real words; the AI only saw tokens.
                    "evidence_quote_restored": restorer.original(f.get("evidence_quote"), "application_text"),
                    "evidence_span": restorer.locate(f.get("evidence_quote"), "application_text"),
                    "rule_sources": rule_sources(rule),
                    # Officer view: real words restored (the AI only saw tokens).
                    "supporting_quotes_restored": [
                        q | {"quote": restorer.original(q.get("quote"), q.get("source") or "application_text"),
                             "span": restorer.locate(q.get("quote"), q.get("source") or "application_text")}
                        for q in (f.get("supporting_quotes") or [])
                    ],
                    "ai_summaries_restored": [
                        {"text": restorer.text(sm.get("text")), "linked": bool(sm.get("linked")),
                         "passages": [{"quote": restorer.original(ps.get("quote"), ps.get("source") or "application_text"),
                                       "verified": bool(ps.get("verified")),
                                       "span": restorer.locate(ps.get("quote"), ps.get("source") or "application_text")}
                                      for ps in sm.get("passages", [])]}
                        for sm in (f.get("ai_summaries") or [])
                    ],
                    "section": (rule.get("params") or {}).get("section"),
                    "weight": (rule.get("params") or {}).get("weight"),
                    "display_label": "Not valid - do not rely on this" if not f["is_valid"] else None,
                    "latest_review": reviews.get(f["id"]),
                    "review_history": sorted([h for h in history if h["finding_id"] == f["id"]], key=lambda h: h["seq"]),
                }
            )
        facts = store.select("fact_extractions", eq={"run_id": run["id"]})
    return {
        "application": app | _names(store, app),
        "applicant": one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)),
        "documents": store.select("documents", eq={"application_id": application_id}),
        "source_texts": restorer.source_texts() if run else {},
        "latest_run": run,
        "runs": store.select("assessment_runs", eq={"application_id": application_id}, order="started_at", desc=True),
        "facts": facts,
        "findings": findings_out,
        "clarification_requests": store.select("clarification_requests", eq={"application_id": application_id}),
        "letters": store.select("letters", eq={"application_id": application_id}, order="version"),
        "sign_off": one(store.select("sign_offs", eq={"application_id": application_id}, limit=1)),
        "attention": _attention(store, app),
        "consistency": consistency_service.application_flags(store, app, settings or get_settings()),
        "disclaimer": "AI output is a suggestion only. An officer decides every finding and signs off the decision.",
    }


def applicant_view(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    """Applicants never see findings or audit data; letters only once approved."""
    app = application_for_applicant(store, actor, application_id)
    return {
        "application": {k: app.get(k) for k in ("id", "grant_program_id", "status", "submitted_at", "application_text", "manual_assessment_requested")},
        "documents": [
            {k: d.get(k) for k in ("id", "file_name", "declared_type", "uploaded_at")}
            for d in store.select("documents", eq={"application_id": application_id})
        ],
        "letters": store.select("letters", eq={"application_id": application_id, "status": "approved"}),
        "clarification_requests": store.select("clarification_requests", eq={"application_id": application_id, "status": "sent"}),
    }
