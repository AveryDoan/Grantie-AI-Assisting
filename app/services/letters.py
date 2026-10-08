"""Reasons letters (pipeline step 10).

Built ONLY from findings an officer has decided (confirm/override) in the
latest run; AI suggestions that were never reviewed are not used. The text
is assembled by a fixed template (no LLM), so the applicant's quotes are
reproduced exactly and nothing outside the rule pack is invented.

Per reason: what the rule requires; what you wrote (quoted); why this did not
meet the rule; what would change the outcome (from the rule itself); how to
ask for a review (from letter_config).

Code checks then run and are stored: quotes match the application, every
reason cites a confirmed rule, reading grade (textstat), and a quality
checklist. The officer must approve before the letter's status changes;
approval also requires the application to be signed off.
"""

from __future__ import annotations

import re
from typing import Any

import textstat

from app.pipeline.redaction import render_source_text
from app.pipeline.verification import verify_quote
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, now_iso, one

REASON_HEADER = re.compile(r"^Reason (\d+): rule (\S+)\b", re.M)
QUOTE_LINE = re.compile(r'^What you wrote: "(.*)"\s*$', re.M)
TARGET_GRADE = 9.0  # about Year 8; textstat's Flesch-Kincaid grade


def _restore(text: str | None, mapping: dict[str, str]) -> str | None:
    if text is None:
        return None
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text


def _what_would_change(rule: dict[str, Any]) -> str:
    explicit = (rule.get("params") or {}).get("what_would_change")
    if explicit:
        return explicit
    return f"Your application would need to show this: {rule['rule_text']}"


def _what_you_wrote(reason: dict[str, Any]) -> str:
    if reason["quote"]:
        return f'What you wrote: "{reason["quote"]}"'
    if reason["finding"].get("check_source") == "code" and reason["rule"].get("rule_type") in ("document_based", "cross_application"):
        return "What you wrote: We checked this using the documents and records you gave us, not your written answers."
    return "What you wrote: We did not find anything about this in your application."


def _why(finding: dict[str, Any], review: dict[str, Any]) -> str:
    if review.get("reason"):
        return review["reason"]
    if review["final_status"] == "Needs evidence":
        return "Your application did not give us enough information to check this."
    if finding.get("check_source") == "code" and finding.get("rationale"):
        return finding["rationale"]
    return "The information in your application did not show that this rule was met."


def compose_letter(
    *,
    program_name: str,
    pack_version: str,
    reasons: list[dict[str, Any]],
    letter_config: dict[str, Any],
) -> str:
    review_process = letter_config.get("review_process", "[review process text]")
    contact = letter_config.get("contact", "[contact details]")
    deadline = letter_config.get("review_deadline", "[review deadline wording]")
    lines = [
        f"About your application to {program_name}",
        "",
        "Dear applicant,",
        "",
        f"Thank you for applying for {program_name}. An officer has checked your application against "
        f"the published rules (rule pack {pack_version}). This letter explains each rule your application "
        "did not meet, or where we need more information.",
        "",
    ]
    for i, r in enumerate(reasons, start=1):
        rule = r["rule"]
        clause = f" ({rule['source_clause']})" if rule.get("source_clause") else ""
        lines += [
            f"Reason {i}: rule {rule['rule_code']}",
            f"What the rule requires: {rule['rule_text']}{clause}",
            _what_you_wrote(r),
            f"Why this did not meet the rule: {r['why']}",
            f"What would change the outcome: {_what_would_change(rule)}",
            f"How to ask for a review: {review_process}",
            "",
        ]
    lines += [
        "How to ask for a review",
        f"{review_process} {deadline}",
        f"Contact: {contact}",
        "",
        "A person made this decision. Computer tools only helped the officer find information.",
    ]
    return "\n".join(lines)


def run_letter_checks(
    body: str,
    *,
    source_text: str,
    confirmed_rule_codes: set[str],
    letter_config: dict[str, Any],
) -> dict[str, Any]:
    cited = REASON_HEADER.findall(body)
    cited_codes = [code for _, code in cited]
    quotes = QUOTE_LINE.findall(body)
    unmatched = [q for q in quotes if not verify_quote(q, source_text, threshold=100.0).verified]
    uncited = [c for c in cited_codes if c not in confirmed_rule_codes]

    # Checklist per reason block.
    blocks = re.split(r"^Reason \d+: ", body, flags=re.M)[1:]
    review_text = letter_config.get("review_process", "")
    checklist = []
    for block in blocks:
        code = block.split()[1] if len(block.split()) > 1 else "?"
        checklist.append(
            {
                "rule": code,
                "cites_rule": "What the rule requires:" in block and code in confirmed_rule_codes,
                "quotes_applicant": bool(re.search(r'^What you wrote: (".+"|We did not find|We checked)', block, re.M)),
                "explains_link": bool(re.search(r"^Why this did not meet the rule: \S", block, re.M)),
                "says_what_would_change": bool(re.search(r"^What would change the outcome: \S", block, re.M)),
                "includes_review_info": bool(review_text) and review_text in block,
            }
        )

    prose = QUOTE_LINE.sub("", body)  # grade the officer's wording, not the applicant's
    grade = round(float(textstat.flesch_kincaid_grade(prose)), 1)
    lowered = body.lower()
    no_score = not any(w in lowered for w in (" score", "ranking", "ranked", "ai decided", "the ai has decided"))

    result = {
        "quotes_match_application": not unmatched,
        "unmatched_quotes": unmatched,
        "every_reason_cites_confirmed_rule": bool(cited_codes) and not uncited,
        "uncited_or_unconfirmed_rules": uncited,
        "reading_grade": grade,
        "reading_grade_target": TARGET_GRADE,
        "reading_grade_ok": grade <= TARGET_GRADE,
        "no_score_or_ranking_language": no_score,
        "checklist": checklist,
        "includes_review_info": bool(review_text) and review_text in body and letter_config.get("contact", "") in body,
    }
    result["all_passed"] = (
        result["quotes_match_application"]
        and result["every_reason_cites_confirmed_rule"]
        and result["reading_grade_ok"]
        and result["no_score_or_ranking_language"]
        and result["includes_review_info"]
        and all(all(v for k, v in c.items() if k != "rule") for c in checklist)
    )
    return result


def _context(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    run = latest_run(store, app["id"])
    if run is None:
        raise Conflict("There is no complete assessment run for this application")
    findings = store.select("findings", eq={"run_id": run["id"]})
    reviews = latest_reviews(store, [f["id"] for f in findings])
    pending = [f for f in findings if f["id"] not in reviews or reviews[f["id"]]["action"] == "ask_applicant"]
    pack = one(store.select("rule_packs", eq={"id": run["rule_pack_id"]}, limit=1)) or {}
    program = one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}
    rules = {r["id"]: r for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})}
    redaction = one(store.select("redaction_maps", eq={"application_id": app["id"]}, limit=1))
    confirmed = {rules[f["rule_id"]]["rule_code"] for f in findings if f["id"] in reviews and reviews[f["id"]]["action"] != "ask_applicant"}
    return {
        "run": run,
        "findings": findings,
        "reviews": reviews,
        "pending": pending,
        "pack": pack,
        "program": program,
        "rules": rules,
        "mapping": (redaction or {}).get("mapping") or {},
        "confirmed_codes": confirmed,
        "source_text": render_source_text(app.get("application_text") or {}),
    }


def generate_letter(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    ctx = _context(store, app)
    if ctx["pending"]:
        raise Conflict(
            "Every finding needs an officer decision before a letter can be drafted",
            details={"pending_findings": [f["id"] for f in ctx["pending"]]},
        )

    reasons = []
    for f in ctx["findings"]:
        review = ctx["reviews"][f["id"]]
        if review["final_status"] not in ("Not met", "Needs evidence"):
            continue
        rule = ctx["rules"][f["rule_id"]]
        quote = _restore(f.get("evidence_quote"), ctx["mapping"]) if f.get("quote_verified") else None
        if quote and not verify_quote(quote, ctx["source_text"], threshold=100.0).verified:
            quote = None  # never put words in the applicant's mouth
        reasons.append({"rule": rule, "finding": f, "quote": quote, "why": _why(f, review)})
    if not reasons:
        raise Conflict("No officer-confirmed unmet rules: a reasons letter is not needed")
    reasons.sort(key=lambda r: (r["rule"].get("display_order", 0), r["rule"]["rule_code"]))

    letter_config = ctx["pack"].get("letter_config") or {}
    body = compose_letter(
        program_name=ctx["program"].get("name", "the grant program"),
        pack_version=ctx["run"]["rule_pack_version"],
        reasons=reasons,
        letter_config=letter_config,
    )
    checks = run_letter_checks(body, source_text=ctx["source_text"], confirmed_rule_codes=ctx["confirmed_codes"],
                               letter_config=letter_config)
    existing = store.select("letters", eq={"application_id": application_id})
    version = max((l["version"] for l in existing), default=0) + 1
    letter = store.insert(
        "letters",
        {
            "application_id": application_id,
            "version": version,
            "body_text": body,
            "reading_grade": checks["reading_grade"],
            "quality_checks": checks,
            "source_finding_ids": [r["finding"]["id"] for r in reasons],
            "status": "draft",
        },
    )[0]
    write_audit(store, actor, "letter.generated", application_id=application_id,
                rule_pack_version=ctx["run"]["rule_pack_version"],
                details={"letter_id": letter["id"], "version": version, "reasons": len(reasons),
                         "all_checks_passed": checks["all_passed"], "reading_grade": checks["reading_grade"]})
    return letter


def update_letter(
    store: Store, actor: Actor, letter_id: str, *, body_text: str | None = None, approve: bool = False
) -> dict[str, Any]:
    require_role(actor, "officer")
    letter = one(store.select("letters", eq={"id": letter_id}, limit=1))
    if not letter:
        raise NotFound("Letter not found")
    app = application_for_staff(store, actor, letter["application_id"])
    if letter["status"] == "approved":
        raise Conflict("This letter is approved and cannot be changed; generate a new version")
    if body_text is None and not approve:
        raise ValidationFailed("Provide body_text to edit, or approve=true")

    ctx = _context(store, app)
    letter_config = ctx["pack"].get("letter_config") or {}
    body = body_text if body_text is not None else letter["body_text"]
    checks = run_letter_checks(body, source_text=ctx["source_text"], confirmed_rule_codes=ctx["confirmed_codes"],
                               letter_config=letter_config)
    values: dict[str, Any] = {"body_text": body, "quality_checks": checks, "reading_grade": checks["reading_grade"]}
    if body_text is not None:
        values |= {"status": "edited", "edited_by": actor.user_id}

    if approve:
        if not checks["quotes_match_application"] or not checks["every_reason_cites_confirmed_rule"]:
            raise Conflict("The letter fails a required check and cannot be approved", details={"quality_checks": checks})
        if app["status"] != "signed_off":
            raise Conflict("Sign off the application before approving its letter")
        values |= {"status": "approved", "approved_by": actor.user_id, "approved_at": now_iso()}

    updated = store.update("letters", values, eq={"id": letter_id})[0]
    write_audit(
        store,
        actor,
        "letter.approved" if approve else "letter.edited",
        application_id=app["id"],
        details={"letter_id": letter_id, "version": letter["version"], "all_checks_passed": checks["all_passed"]},
    )
    return updated
