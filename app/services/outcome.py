"""Step 4: Outcome.

- Any eligibility rule the officer confirmed as Not met -> a decline letter (drafted when that finding is confirmed): it names each
  unmet rule, quotes the applicant's own words (restored, not tokens), says in plain English what would need to be different, and
  how to ask for a review. No scores, no comparisons.
- Otherwise the application is eligible: a summary for the panel (the officer's marks and reasons, the signals the officer kept) and
  a next-steps letter to review and approve. No total, average or ranking is calculated.
- A letter is released only after the officer signs off. There is no "approve all" and no bulk sign-off.
- After sign-off the record is locked. Reopening needs a reason and makes the next sign-off a new version.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services import letters
from app.services.access import Actor, application_for_staff, require_role
from app.services.consistency_overview import family_label
from app.services.review import SIGN_OFF_STATEMENT, latest_reviews, latest_run, undecided_rules
from app.store.base import Store, one


def _merit_rules(rules: dict[str, Any]) -> set[str]:
    return {i for i, r in rules.items() if (r.get("params") or {}).get("section") == "merit"}


def build(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    run = latest_run(store, application_id)
    out: dict[str, Any] = {"application_id": application_id, "statement": SIGN_OFF_STATEMENT, "signed_off": app["status"] == "signed_off"}
    if run is None:
        return out | {"result": "not_assessed", "unmet": [], "needs_information": [], "letter": None, "summary": None, "record": None}
    _, pending = undecided_rules(store, application_id)
    ctx = letters._context(store, app, settings)
    merit_ids = _merit_rules(ctx["rules"])
    reviews = latest_reviews(store, [f["id"] for f in ctx["findings"]])
    unmet, more = [], []
    for r in letters._decline_reasons(ctx):
        rv = reviews[r["finding"]["id"]]
        params = r["rule"].get("params") or {}
        item = {"rule_code": r["rule"]["rule_code"], "rule_text": r["rule"]["rule_text"], "final_status": rv["final_status"],
                "finding_id": r["finding"]["id"],
                # a missing or wrong document the applicant could send: the officer may ask instead of declining
                "fixable_by_document": r["rule"].get("rule_type") == "document_based" or bool(params.get("required_documents")),
                "reason": r["why"], "quote": r["quote"], "what_would_change": letters._what_would_change(r["rule"])}
        (unmet if rv["final_status"] == "Not met" else more).append(item)
    # Anything the officer settled as Unclear also needs a person to go back to the applicant.
    for f in ctx["findings"]:
        rv = reviews.get(f["id"])
        if rv and rv["final_status"] == "Unclear" and f["rule_id"] not in merit_ids:
            rule = ctx["rules"][f["rule_id"]]
            more.append({"rule_code": rule["rule_code"], "rule_text": rule["rule_text"], "final_status": "Unclear", "reason": rv.get("reason") or "", "quote": None, "what_would_change": ""})
    from app.services import outcome_flow

    result = "decline" if unmet else "undecided" if pending else "needs_information" if more else "eligible"
    if result == "decline" and pending:
        result = "undecided"          # keep checking, or go to Outcome now: the officer chooses
    kind = outcome_flow.letter_kind(store, app) if result in ("decline", "eligible") else None
    decision = outcome_flow.latest_decision(store, application_id)
    awaiting_round = result == "eligible" and outcome_flow.competitive(outcome_flow.pack_config(store, app)) and decision is None
    all_letters = sorted(store.select("letters", eq={"application_id": application_id}), key=lambda l: l["version"])
    mine = [l for l in all_letters if l.get("kind", "decline") == kind] if kind else []
    summary = None
    if result == "eligible":
        marks = []
        for m in store.select("merit_marks", eq={"application_id": application_id}):
            rule = next((r for r in ctx["rules"].values() if r["rule_code"] == m["rule_code"]), {})
            marks.append({"rule_code": m["rule_code"], "criterion": rule.get("rule_text", m["rule_code"]), "mark": m["mark"],
                          "not_assessed": m["not_assessed"], "reason": m.get("reason")})
        kept = [{"label": family_label(f["check_id"], f["check_type"]), "note": f.get("note")}
                for f in store.select("consistency_flags", eq={"application_id": application_id}) if f["status"] == "confirmed"]
        summary = {"marks": sorted(marks, key=lambda m: m["rule_code"]), "signals_kept": kept,
                   "note": "These are the officer's own marks and reasons. Nothing is added up, averaged or ranked."}
    record = None
    so = one(store.select("sign_offs", eq={"application_id": application_id}, limit=1))
    if so:
        profile = one(store.select("profiles", eq={"id": so["officer_id"]}, limit=1)) or {}
        history = store.select("sign_off_history", eq={"application_id": application_id})
        decisions = []
        for f in ctx["findings"]:
            rule = ctx["rules"][f["rule_id"]]
            rv = reviews.get(f["id"])
            if f["rule_id"] in merit_ids:
                mk = next((m for m in store.select("merit_marks", eq={"application_id": application_id}) if m["rule_code"] == rule["rule_code"]), None)
                decisions.append({"rule_code": rule["rule_code"], "rule_text": rule["rule_text"], "decision": "Not assessed" if mk and mk["not_assessed"] else f"Marked {mk['mark']}" if mk else "—", "ai_status": f["ai_status"], "reason": mk.get("reason") if mk else None})
            else:
                decisions.append({"rule_code": rule["rule_code"], "rule_text": rule["rule_text"], "decision": rv["final_status"] if rv else "—",
                                  "ai_status": f["ai_status"], "reason": rv.get("reason") if rv else None,
                                  "overridden": bool(rv and rv["action"] == "override" and rv["final_status"] != f["ai_status"])})
        record = {"outcome": result, "officer": profile.get("display_name") or "Officer", "signed_at": so["signed_at"],
                  "version": len(history) + 1, "letter_version": mine[-1]["version"] if mine else None,
                  "letter_status": mine[-1]["status"] if mine else None, "decisions": sorted(decisions, key=lambda d: d["rule_code"]),
                  "reopened": [{"version": h["version"], "reopened_at": h["reopened_at"], "reason": h["reason"]} for h in sorted(history, key=lambda h: h["version"])]}
    letter = mine[-1] if mine else None
    cond = outcome_flow.sign_off_conditions(store, actor, app, settings) if result in ("decline", "eligible") else None
    merit_reasons = []
    if kind == "not_selected":
        for m in store.select("merit_marks", eq={"application_id": application_id}):
            rule = next((r for r in ctx["rules"].values() if r["rule_code"] == m["rule_code"]), {})
            if (m.get("reason") or "").strip():
                merit_reasons.append({"rule_code": m["rule_code"], "criterion": letters.criterion_title(rule) if rule else m["rule_code"],
                                      "reason": m["reason"]})
    return out | {"result": result, "unmet": unmet, "needs_information": more, "letter": letter,
                  "letter_kind": kind, "summary": summary, "record": record, "pending_rules": pending,
                  "outcome_status": app.get("outcome_status") or "in_progress",
                  "not_assessed": sorted(outcome_flow.skipped_steps(store, application_id)),
                  "not_assessed_label": outcome_flow.NOT_ASSESSED,
                  "could_change": outcome_flow.could_change(store, application_id) if unmet else [],
                  "awaiting_round_decision": awaiting_round,
                  "round_decision": {k: decision[k] for k in ("decision", "decided_by", "authority", "decided_at", "reference", "reason_note", "version")}
                                    if decision else None,
                  "include_marks_in_letter": bool(outcome_flow.pack_config(store, app).get("include_marks_in_letter", False)),
                  "feedback_options": sorted(merit_reasons, key=lambda m: m["rule_code"]),
                  "feedback_chosen": ((letter or {}).get("quality_checks") or {}).get("feedback_criteria", []) if kind == "not_selected" else [],
                  "letter_matches_findings": letters.matches_findings(store, letter, settings) if letter else None,
                  "conditions": cond}
