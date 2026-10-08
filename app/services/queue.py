"""Officer work queue and application detail views.

The queue is ordered by the officer's open work items (unreviewed findings,
errors, invalid quotes, flags), NOT by any judgement about the applicant.
No view contains an eligibility score or an approve/reject recommendation.
"""

from __future__ import annotations

from typing import Any

from app.services.access import Actor, application_for_applicant, application_for_staff, staff_can_access_program
from app.services.errors import Forbidden
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, one


def reference(application_id: str) -> str:
    """Short human-readable reference for an application (display only)."""
    return "APP-" + application_id.replace("-", "")[:6].upper()


def _names(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)) or {}
    program = one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}
    return {
        "reference": reference(app["id"]),
        "applicant_name": applicant.get("organisation_name") or applicant.get("display_name"),
        "program_name": program.get("name"),
    }


def _attention(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    run = latest_run(store, app["id"])
    failed = store.select("assessment_runs", eq={"application_id": app["id"], "status": "failed"}, limit=1)
    docs = store.select("documents", eq={"application_id": app["id"]})
    items: dict[str, Any] = {
        "not_yet_assessed": run is None and not app.get("manual_assessment_requested"),
        "manual_assessment_requested": bool(app.get("manual_assessment_requested")),
        "failed_runs": len(failed),
        "documents_needing_verification": sum(bool(d.get("needs_verification")) for d in docs),
        "unreviewed_findings": 0,
        "invalid_findings": 0,
        "error_findings": 0,
        "language_flags": 0,
        "injection_flags": 0,
        "awaiting_applicant": app["status"] == "awaiting_applicant",
    }
    if run:
        findings = store.select("findings", eq={"run_id": run["id"]})
        reviews = latest_reviews(store, [f["id"] for f in findings])
        items["unreviewed_findings"] = sum(
            1 for f in findings if f["id"] not in reviews or reviews[f["id"]]["action"] == "ask_applicant"
        )
        items["invalid_findings"] = sum(not f["is_valid"] for f in findings)
        items["error_findings"] = sum(bool(f["error_flag"]) for f in findings)
        items["language_flags"] = sum(bool(f["language_flag"]) for f in findings)
        items["injection_flags"] = len(run.get("injection_flags") or [])
    return items


def _open_items(a: dict[str, Any]) -> int:
    return sum(int(v) for k, v in a.items() if k != "awaiting_applicant")


def list_queue(store: Store, actor: Actor) -> list[dict[str, Any]]:
    if not actor.is_staff:
        raise Forbidden("Officers only")
    out = []
    for app in store.select("applications"):
        if app["status"] == "draft" or not staff_can_access_program(store, actor, app["grant_program_id"]):
            continue
        attention = _attention(store, app)
        out.append(
            {
                "id": app["id"],
                **_names(store, app),
                "grant_program_id": app["grant_program_id"],
                "status": app["status"],
                "submitted_at": app.get("submitted_at"),
                "attention": attention,
                "open_items": _open_items(attention) if app["status"] != "signed_off" else 0,
            }
        )
    # Most open work first, then oldest submission first. Not a ranking of applicants.
    out.sort(key=lambda r: (r["status"] == "signed_off", -r["open_items"], r["submitted_at"] or ""))
    return out


def _restore(text: str | None, mapping: dict[str, str]) -> str | None:
    if text is None:
        return None
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text


def application_detail(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    if actor.role == "applicant":
        return applicant_view(store, actor, application_id)
    app = application_for_staff(store, actor, application_id)
    run = latest_run(store, application_id)
    mapping = (one(store.select("redaction_maps", eq={"application_id": application_id}, limit=1)) or {}).get("mapping") or {}
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
                    "evidence_quote_restored": _restore(f.get("evidence_quote"), mapping),
                    "supporting_quotes_restored": [
                        q | {"quote": _restore(q.get("quote"), mapping)} for q in (f.get("supporting_quotes") or [])
                    ],
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
        "latest_run": run,
        "runs": store.select("assessment_runs", eq={"application_id": application_id}, order="started_at", desc=True),
        "facts": facts,
        "findings": findings_out,
        "clarification_requests": store.select("clarification_requests", eq={"application_id": application_id}),
        "letters": store.select("letters", eq={"application_id": application_id}, order="version"),
        "sign_off": one(store.select("sign_offs", eq={"application_id": application_id}, limit=1)),
        "attention": _attention(store, app),
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
