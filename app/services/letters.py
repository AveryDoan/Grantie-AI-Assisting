"""Outcome letters: decline (not eligible), not selected (competitive) and next steps (selected / eligible).

Built ONLY from what an officer decided: confirmed Not met findings (decline), the officer's own marking reasons that the
officer chose to include (not selected), and the grant's letter settings. The text is assembled by a fixed template in code
(no language model), so the applicant's quotes are reproduced exactly and nothing outside the rule pack and the officer's
own words is invented. Every letter is marked `template` for that reason.

Decline, per reason: what the rule requires; what you wrote (quoted); why this did not meet the rule; what would change the
outcome; how to ask for a review.

Code checks run on every draft and every edit (letter_checks): quotes match the application, every reason cites a rule an
officer confirmed as Not met, forbidden terms, numbers from allowed sources, no unresolved placeholders, no comment on
grammar or style, instruction-like quotes flagged, and the reading level. A letter is released only after sign-off, after
the officer has read it to the end, and when every blocking check passes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import Settings, get_settings
from app.pipeline.verification import verify_quote
from app.services import letter_checks
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict, NotFound, ValidationFailed
from app.services.redaction_service import QuoteRestorer
from app.services.review import latest_reviews, latest_run
from app.store.base import Store, now_iso, one

log = logging.getLogger(__name__)

REASON_HEADER = re.compile(r"^Reason (\d+): rule (\S+)\b", re.M)
QUOTE_LINE = letter_checks.QUOTE_LINE
TARGET_GRADE = letter_checks.TARGET_GRADE
NOT_SELECTED_FEEDBACK = re.compile(r"^- (.+?): (.+)$", re.M)


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


def _round_line(letter_config: dict[str, Any], program_name: str) -> str:
    rnd = (letter_config.get("round") or {}).get("name")
    return f"{program_name}, {rnd}" if rnd else program_name


def compose_letter(*, program_name: str, pack_version: str, reasons: list[dict[str, Any]], letter_config: dict[str, Any]) -> str:
    review_process = letter_config.get("review_process", "[review process text]")
    contact = letter_config.get("contact", "[contact details]")
    deadline = letter_config.get("review_deadline", "[review deadline wording]")
    again = letter_config.get("apply_again", "[whether you can apply again in a future round]")
    lines = [
        f"About your application to {program_name}",
        "",
        "Dear applicant,",
        "",
        f"Thank you for applying. Your application is not eligible for {_round_line(letter_config, program_name)}.",
        f"An officer checked your application against the published rules (rule pack {pack_version}). "
        "This letter explains each rule your application did not meet.",
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
        "Applying again",
        again,
        "",
        "How to ask for a review",
        f"{review_process} {deadline}",
        f"Contact: {contact}",
        "",
        "A person made this decision. Computer tools only helped the officer find information.",
    ]
    return "\n".join(lines)


def _has_review_and_contact(body: str) -> bool:
    """The letter says how to ask for a review and who to contact (the officer may have replaced a placeholder with the real text)."""
    return bool(re.search(r"^How to ask for a review\s*$\n\S", body, re.M)) and _has_contact(body)


def _has_contact(body: str) -> bool:
    return bool(re.search(r"^Contact: \S", body, re.M))


def _sources(letter_config: dict[str, Any], *extra: Any) -> list[str]:
    return list(letter_checks.flatten(letter_config)) + list(letter_checks.flatten(list(extra)))


def run_letter_checks(body: str, *, source_text: str, confirmed_rule_codes: set[str], letter_config: dict[str, Any],
                      allowed_sources: list[str] | None = None) -> dict[str, Any]:
    """`confirmed_rule_codes`: the rules an officer confirmed as Not met. A reason citing anything else fails."""
    cited_codes = [code for _, code in REASON_HEADER.findall(body)]
    quotes = QUOTE_LINE.findall(body)
    unmatched = [q for q in quotes if not verify_quote(q, source_text, threshold=100.0).verified]
    uncited = [c for c in cited_codes if c not in confirmed_rule_codes]

    blocks = re.split(r"^Reason \d+: ", body, flags=re.M)[1:]
    checklist = []
    for block in blocks:
        code = block.split()[1] if len(block.split()) > 1 else "?"
        checklist.append({
            "rule": code,
            "cites_rule": "What the rule requires:" in block and code in confirmed_rule_codes,
            "quotes_applicant": bool(re.search(r'^What you wrote: (".+"|We did not find|We checked)', block, re.M)),
            "explains_link": bool(re.search(r"^Why this did not meet the rule: \S", block, re.M)),
            "says_what_would_change": bool(re.search(r"^What would change the outcome: \S", block, re.M)),
            "includes_review_info": bool(re.search(r"^How to ask for a review: \S", block, re.M)),
        })
    code_checks = letter_checks.check(body, allowed_sources=(allowed_sources or []) + quotes, quotes=quotes)
    result = {
        "quotes_match_application": not unmatched,
        "unmatched_quotes": unmatched,
        "every_reason_cites_confirmed_rule": bool(cited_codes) and not uncited,
        "uncited_or_unconfirmed_rules": uncited,
        "checklist": checklist,
        "includes_review_info": _has_review_and_contact(body),
        **code_checks,
    }
    result["all_passed"] = (
        result["quotes_match_application"] and result["every_reason_cites_confirmed_rule"] and result["includes_review_info"]
        and all(all(v for k, v in c.items() if k != "rule") for c in checklist)
        and all(result[k] for k in letter_checks.BLOCKING)
    )
    return result


def _context(store: Store, app: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    from redaction.structured import original_application_text
    run = latest_run(store, app["id"])
    if run is None:
        raise Conflict("There is no complete assessment run for this application")
    findings = store.select("findings", eq={"run_id": run["id"]})
    reviews = latest_reviews(store, [f["id"] for f in findings])
    rules = {r["id"]: r for r in store.select("rules", eq={"rule_pack_id": run["rule_pack_id"]})}
    marked = {m["rule_code"] for m in store.select("merit_marks", eq={"application_id": app["id"]})}

    def is_merit(f: dict[str, Any]) -> bool:
        return (rules.get(f["rule_id"], {}).get("params") or {}).get("section") == "merit"

    pending = [f for f in findings if is_merit(f) and rules[f["rule_id"]]["rule_code"] not in marked] + [
        f for f in findings if not is_merit(f) and (f["id"] not in reviews or reviews[f["id"]]["action"] == "ask_applicant")]
    not_met = [f for f in findings if not is_merit(f) and f["id"] in reviews
               and reviews[f["id"]]["action"] != "ask_applicant" and reviews[f["id"]]["final_status"] == "Not met"]
    pack = one(store.select("rule_packs", eq={"id": run["rule_pack_id"]}, limit=1)) or {}
    program = one(store.select("grant_programs", eq={"id": app["grant_program_id"]}, limit=1)) or {}
    return {
        "app": app,
        "run": run,
        "findings": findings,
        "reviews": reviews,
        "pending": pending,
        "pack": pack,
        "program": program,
        "rules": rules,
        "is_merit": is_merit,
        # Quotes in findings contain tokens; this maps them back to the applicant's own words.
        "restorer": QuoteRestorer(store, app, settings or get_settings()),
        "not_met_ids": [f["id"] for f in not_met],
        "not_met_codes": {rules[f["rule_id"]]["rule_code"] for f in not_met},
        "source_text": original_application_text(app.get("application_text") or {}),
        "app_text": app.get("application_text") or {},
    }


def _typed_answer(ctx: dict[str, Any], rule: dict[str, Any]) -> str | None:
    """For a rule checked by code from a typed answer (a date, a country), the applicant's own entry, exactly as they typed it.
    Only used when it appears word for word in the application text."""
    from app.services.queue import rule_sources

    fields = ((ctx.get("app_text") or {}).get("fields") or {}) | ((ctx.get("app_text") or {}).get("answers") or {})
    for src in rule_sources(rule):
        value = str(fields.get(src["typed"]) or "").strip() if src.get("typed") else ""
        if len(value) >= 3 and verify_quote(value, ctx["source_text"], threshold=100.0).verified:
            return value
    return None


def _reasons(ctx: dict[str, Any], statuses: tuple[str, ...] = ("Not met",)) -> list[dict[str, Any]]:
    """Officer-decided eligibility findings with one of `statuses`, in rule order, with the applicant's own words."""
    out = []
    for f in ctx["findings"]:
        review = ctx["reviews"].get(f["id"])
        if review is None or review["action"] == "ask_applicant" or review["final_status"] not in statuses or ctx["is_merit"](f):
            continue
        rule = ctx["rules"][f["rule_id"]]
        quote = ctx["restorer"].original(f.get("evidence_quote"), "application_text") if f.get("quote_verified") else None
        if quote and not verify_quote(quote, ctx["source_text"], threshold=100.0).verified:
            quote = None  # never put words in the applicant's mouth
        if not quote:
            quote = _typed_answer(ctx, rule)
        out.append({"rule": rule, "finding": f, "quote": quote, "why": _why(f, review)})
    out.sort(key=lambda r: (r["rule"].get("display_order", 0), r["rule"]["rule_code"]))
    return out


def _decline_reasons(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Kept for the outcome view: Not met and Needs evidence decisions. A decline LETTER uses Not met only."""
    return _reasons(ctx, ("Not met", "Needs evidence"))


def _decline_checks(ctx: dict[str, Any], body: str, reasons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cfg = ctx["pack"].get("letter_config") or {}
    reasons = reasons if reasons is not None else _reasons(ctx)
    sources = _sources(cfg, ctx["program"].get("name"), ctx["run"].get("rule_pack_version"),
                       [[r["rule"]["rule_text"], r["rule"].get("source_clause"), _what_would_change(r["rule"]), r["why"]] for r in reasons])
    return run_letter_checks(body, source_text=ctx["source_text"], confirmed_rule_codes=ctx["not_met_codes"], letter_config=cfg,
                             allowed_sources=sources)


def _next_version(store: Store, application_id: str) -> int:
    return max((l["version"] for l in store.select("letters", eq={"application_id": application_id})), default=0) + 1


def _insert(store: Store, actor: Actor, app: dict[str, Any], *, kind: str, body: str, checks: dict[str, Any], source_ids: list[str],
            pack_version: str | None, extra: dict[str, Any] | None = None, redraft: bool = False) -> dict[str, Any]:
    version = _next_version(store, app["id"])
    letter = store.insert("letters", {
        "application_id": app["id"], "version": version, "body_text": body, "reading_grade": checks["reading_grade"],
        "quality_checks": checks | (extra or {}), "source_finding_ids": source_ids, "status": "draft", "kind": kind,
        "checks_passed": checks["all_passed"], "template": True,
    })[0]
    write_audit(store, actor, "letter.redrafted" if redraft else "letter.generated", application_id=app["id"], rule_pack_version=pack_version,
                details={"letter_id": letter["id"], "version": version, "reasons": len(source_ids), "kind": kind,
                         "all_checks_passed": checks["all_passed"], "reading_grade": checks["reading_grade"]})
    return letter


def _store_letter(store: Store, actor: Actor, app: dict[str, Any], ctx: dict[str, Any], reasons: list[dict[str, Any]],
                  redraft: bool = False) -> dict[str, Any]:
    cfg = ctx["pack"].get("letter_config") or {}
    body = compose_letter(program_name=ctx["program"].get("name", "the grant program"), pack_version=ctx["run"]["rule_pack_version"],
                          reasons=reasons, letter_config=cfg)
    return _insert(store, actor, app, kind="decline", body=body, checks=_decline_checks(ctx, body, reasons),
                   source_ids=[r["finding"]["id"] for r in reasons], pack_version=ctx["run"]["rule_pack_version"], redraft=redraft)


def latest_of(store: Store, application_id: str, kind: str) -> dict[str, Any] | None:
    return max((l for l in store.select("letters", eq={"application_id": application_id}) if l.get("kind", "decline") == kind),
               key=lambda l: l["version"], default=None)


def generate_letter(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Draft the decline letter now. It lists exactly the findings an officer confirmed as Not met."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    ctx = _context(store, app, settings)
    from app.services import outcome_flow

    if ctx["pending"] and not outcome_flow.on_ineligible_path(store, application_id):
        raise Conflict("Every finding needs an officer decision before a letter can be drafted",
                       details={"pending_findings": [f["id"] for f in ctx["pending"]]})
    reasons = _reasons(ctx)
    if not reasons:
        raise Conflict("No rule has been confirmed as Not met by an officer: a decline letter is not needed")
    return _store_letter(store, actor, app, ctx, reasons)


def redraft(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """After any officer decision: keep the decline letter in step with the confirmed Not met findings.

    Drafted at the first Not met, redrafted when another Not met is confirmed or one is reversed. A letter the officer edited is
    never overwritten: it shows that the findings changed and asks the officer to redraft. Nothing is ever released here."""
    app = application_for_staff(store, actor, application_id)
    try:
        ctx = _context(store, app, settings)
    except Conflict:
        return None
    latest = latest_of(store, application_id, "decline")
    if latest and (latest["status"] in ("edited", "approved") or latest.get("released_at")):
        return latest
    reasons = _reasons(ctx)
    if not reasons:
        return latest
    if latest and sorted(latest.get("source_finding_ids") or []) == sorted(r["finding"]["id"] for r in reasons):
        return latest
    return _store_letter(store, actor, app, ctx, reasons, redraft=latest is not None)


# Kept under its old name: called when an officer confirms a rule as Not met.
draft_on_confirmation = redraft


def matches_findings(store: Store, letter: dict[str, Any], settings: Settings | None = None) -> bool:
    """A decline letter cites exactly the findings an officer currently has as Not met (an edited letter can fall behind)."""
    if letter.get("kind", "decline") != "decline":
        return True
    app = one(store.select("applications", eq={"id": letter["application_id"]}, limit=1))
    ctx = _context(store, app, settings)
    return sorted(letter.get("source_finding_ids") or []) == sorted(ctx["not_met_ids"])


# ---------------------------------------------------------------- the "next steps" letter (selected, or eligible in an open round)


def _next_steps_checks(body: str, letter_config: dict[str, Any], *extra: Any) -> dict[str, Any]:
    code_checks = letter_checks.check(body, allowed_sources=_sources(letter_config, *extra))
    result = {
        "quotes_match_application": True, "unmatched_quotes": [], "every_reason_cites_confirmed_rule": True,
        "uncited_or_unconfirmed_rules": [], "checklist": [], "includes_review_info": _has_contact(body), **code_checks,
    }
    result["all_passed"] = result["includes_review_info"] and all(result[k] for k in letter_checks.BLOCKING)
    return result


run_next_steps_checks = _next_steps_checks


def generate_next_steps(store: Store, actor: Actor, application_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """The letter for an application whose eligibility rules are all met: the offer / next steps. A draft until released.
    In a competitive round it needs the people's decision "Selected" first."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    ctx = _context(store, app, settings)
    if ctx["pending"]:
        raise Conflict("Every rule needs your decision before this letter can be drafted",
                       details={"pending_findings": [f["id"] for f in ctx["pending"]]})
    if _reasons(ctx, ("Not met", "Needs evidence")):
        raise Conflict("An unmet rule needs a decline letter or a request for information, not a next-steps letter")
    from app.services import outcome_flow

    cfg = ctx["pack"].get("letter_config") or {}
    if outcome_flow.competitive(cfg):
        decision = outcome_flow.latest_decision(store, application_id)
        if not decision or decision["decision"] != "selected":
            raise Conflict("In a competitive round the next-steps letter follows the round decision Selected")
    program = ctx["program"].get("name", "the grant program")
    body = "\n".join([
        f"About your application to {program}", "", "Dear applicant,", "",
        f"Thank you for applying for {_round_line(cfg, program)}. An officer has checked your application against the published rules "
        f"(rule pack {ctx['run']['rule_pack_version']}). Your application meets the rules we check first.", "",
        "What happens next", cfg.get("next_steps", "[next steps and dates]"), "",
        "If you have a question", f"Contact: {cfg.get('contact', '[contact details]')}", "",
        "A person made this decision. Computer tools only helped the officer find information.",
    ])
    checks = _next_steps_checks(body, cfg, program, ctx["run"]["rule_pack_version"])
    return _insert(store, actor, app, kind="next_steps", body=body, checks=checks, source_ids=[], pack_version=ctx["run"]["rule_pack_version"])


# ---------------------------------------------------------------- the "not selected (competitive)" letter


def tidy(text: str) -> str:
    """Wording only (spacing, a capital letter, a full stop). It adds no judgement and changes no meaning."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return t
    t = t[0].upper() + t[1:]
    return t if t[-1] in ".!?" else t + "."


def criterion_title(rule: dict[str, Any]) -> str:
    text = re.sub(r"\s*Weight on the form: \d+%\.?", "", rule.get("rule_text") or rule["rule_code"])
    return re.split(r"\s*[(.]", text, maxsplit=1)[0].strip() or rule["rule_code"]


def compose_not_selected(*, program_name: str, letter_config: dict[str, Any], feedback: list[dict[str, Any]],
                         include_marks: bool) -> str:
    places = letter_config.get("places") or (letter_config.get("round") or {}).get("places") or "[number of places or funding available]"
    contact = letter_config.get("contact", "[contact details]")
    deadline = letter_config.get("review_deadline", "[review deadline wording]")
    review_process = letter_config.get("review_process", "[review process text]")
    future = letter_config.get("future_rounds", "[information about future rounds]")
    lines = [
        f"About your application to {program_name}", "", "Dear applicant,", "",
        f"Thank you for applying for {_round_line(letter_config, program_name)}. We are sorry to tell you that your application "
        "was not selected for funding in this round.", "",
        "Eligibility", "Your application met the eligibility requirements.", "",
        "Why your application was not selected",
        f"This round is competitive. {places} Applications were assessed against the published criteria, and the funding was "
        "offered to the applications the assessment panel recommended.", "",
    ]
    if feedback:
        lines.append("Feedback on your application")
        for item in feedback:
            mark = f" Your mark for this criterion: {item['mark']} out of 100." if include_marks and item.get("mark") is not None else ""
            lines.append(f"- {item['title']}: {tidy(item['reason'])}{mark}")
        lines.append("")
    lines += [
        "What you can do",
        "You can ask us for more feedback on your application.",
        f"If you want a review of how your application was assessed: {review_process} {deadline}",
        future, f"Contact: {contact}", "",
        "People made this decision. Computer tools only helped officers find information.",
    ]
    return "\n".join(lines)


def _not_selected_checks(body: str, cfg: dict[str, Any], feedback: list[dict[str, Any]], include_marks: bool, *extra: Any) -> dict[str, Any]:
    titles = {f["title"] for f in feedback}
    lines = NOT_SELECTED_FEEDBACK.findall(body.split("Feedback on your application", 1)[1].split("What you can do", 1)[0]) \
        if "Feedback on your application" in body else []
    unknown = [t for t, _ in lines if t not in titles]
    sources = _sources(cfg, *extra, [f["reason"] for f in feedback], [f.get("mark") for f in feedback] if include_marks else [])
    code_checks = letter_checks.check(body, allowed_sources=sources)
    result = {
        "quotes_match_application": True, "unmatched_quotes": [], "every_reason_cites_confirmed_rule": True,
        "uncited_or_unconfirmed_rules": [], "checklist": [],
        "feedback_from_officer_reasons": not unknown, "feedback_not_chosen": unknown,
        "includes_review_info": _has_contact(body), **code_checks,
    }
    result["all_passed"] = result["includes_review_info"] and result["feedback_from_officer_reasons"] and all(result[k] for k in letter_checks.BLOCKING)
    return result


def _feedback(store: Store, ctx: dict[str, Any], criteria: list[str]) -> list[dict[str, Any]]:
    marks = {m["rule_code"]: m for m in store.select("merit_marks", eq={"application_id": ctx["app"]["id"]})}
    rules = {r["rule_code"]: r for r in ctx["rules"].values() if (r.get("params") or {}).get("section") == "merit"}
    out = []
    for code in criteria:
        if code not in rules:
            raise ValidationFailed(f"{code} is not a merit criterion of this rule pack")
        m = marks.get(code)
        if not m or not (m.get("reason") or "").strip():
            raise ValidationFailed(f"{code} has no officer reason to use as feedback")
        out.append({"rule_code": code, "title": criterion_title(rules[code]), "reason": m["reason"],
                    "mark": None if m.get("not_assessed") else m.get("mark")})
    return sorted(out, key=lambda f: (rules[f["rule_code"]].get("display_order", 0), f["rule_code"]))


def generate_not_selected(store: Store, actor: Actor, application_id: str, criteria: list[str] | None = None,
                          settings: Settings | None = None) -> dict[str, Any]:
    """The letter after the people's decision "Not selected (competitive)". Feedback is optional: only the officer's own
    marking reasons for the criteria the officer ticks. Marks appear only if the grant's include_marks_in_letter is on."""
    require_role(actor, "officer")
    app = application_for_staff(store, actor, application_id)
    from app.services import outcome_flow

    decision = outcome_flow.latest_decision(store, application_id)
    if not decision or decision["decision"] != "not_selected_competitive" or len((decision.get("reason_note") or "").strip()) < 20:
        raise Conflict("A not-selected letter needs the recorded round decision Not selected, with its reason note")
    ctx = _context(store, app, settings)
    cfg = ctx["pack"].get("letter_config") or {}
    include_marks = bool(cfg.get("include_marks_in_letter", False))
    feedback = _feedback(store, ctx, list(dict.fromkeys(criteria or [])))
    program = ctx["program"].get("name", "the grant program")
    body = compose_not_selected(program_name=program, letter_config=cfg, feedback=feedback, include_marks=include_marks)
    checks = _not_selected_checks(body, cfg, feedback, include_marks, program)
    return _insert(store, actor, app, kind="not_selected", body=body, checks=checks, source_ids=[],
                   pack_version=ctx["run"]["rule_pack_version"], extra={"feedback_criteria": [f["rule_code"] for f in feedback]})


# ---------------------------------------------------------------- checks on the current text, editing, reading, release


def recheck(store: Store, letter: dict[str, Any], body: str, settings: Settings | None = None) -> dict[str, Any]:
    app = one(store.select("applications", eq={"id": letter["application_id"]}, limit=1))
    ctx = _context(store, app, settings)
    cfg = ctx["pack"].get("letter_config") or {}
    kind = letter.get("kind", "decline")
    program = ctx["program"].get("name")
    if kind == "next_steps":
        return _next_steps_checks(body, cfg, program, ctx["run"]["rule_pack_version"])
    if kind == "not_selected":
        codes = (letter.get("quality_checks") or {}).get("feedback_criteria") or []
        feedback = _feedback(store, ctx, codes)
        checks = _not_selected_checks(body, cfg, feedback, bool(cfg.get("include_marks_in_letter", False)), program)
        return checks | {"feedback_criteria": codes}
    # the officer's own wording is an allowed source too (they can write numbers they checked)
    return _decline_checks(ctx, body)


def mark_viewed(store: Store, actor: Actor, letter_id: str) -> dict[str, Any]:
    """The officer opened the letter and scrolled to the end. Needed before sign-off and before any release."""
    require_role(actor, "officer")
    letter = one(store.select("letters", eq={"id": letter_id}, limit=1))
    if not letter:
        raise NotFound("Letter not found")
    application_for_staff(store, actor, letter["application_id"])
    if letter.get("released_at"):
        return letter
    if letter.get("letter_viewed_at") and letter.get("viewed_by") == actor.user_id:
        return letter
    updated = store.update("letters", {"letter_viewed_at": now_iso(), "viewed_by": actor.user_id}, eq={"id": letter_id})[0]
    write_audit(store, actor, "letter.viewed", application_id=letter["application_id"], details={"letter_id": letter_id, "version": letter["version"]})
    return updated


def release_blockers(store: Store, actor: Actor, letter: dict[str, Any], *, signed_off: bool, settings: Settings | None = None) -> list[str]:
    """Plain reasons the letter cannot be released yet (empty when it can)."""
    out = []
    checks = recheck(store, letter, letter["body_text"], settings)
    if not signed_off:
        out.append("Sign off the application first")
    if not letter.get("letter_viewed_at"):
        out.append("Review the letter first: open it and read it to the end")
    if checks["placeholders"]:
        out.append("Fill in every [placeholder] in the letter")
    if not checks["all_passed"]:
        out.append("Fix the letter so every check passes")
    if not matches_findings(store, letter, settings):
        out.append("The findings changed after this letter was written: redraft it")
    return out


def release(store: Store, actor: Actor, letter: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Release one signed-off letter (no email is connected: it is marked ready to send). Every condition is checked again."""
    app = application_for_staff(store, actor, letter["application_id"])
    if letter.get("released_at"):
        return letter
    blockers = release_blockers(store, actor, letter, signed_off=app["status"] == "signed_off", settings=settings)
    if blockers:
        raise Conflict(blockers[0], details={"blockers": blockers})
    so = one(store.select("sign_offs", eq={"application_id": app["id"]}, limit=1)) or {}
    now = now_iso()
    updated = store.update("letters", {
        "status": "approved", "approved_by": actor.user_id, "approved_at": now, "checks_passed": True,
        "signed_off_by": letter.get("signed_off_by") or so.get("officer_id") or actor.user_id,
        "signed_off_at": letter.get("signed_off_at") or so.get("signed_at") or now,
        "released_at": now, "released_by": actor.user_id,
    }, eq={"id": letter["id"]})[0]
    write_audit(store, actor, "letter.released", application_id=app["id"],
                details={"letter_id": letter["id"], "version": letter["version"], "kind": letter.get("kind", "decline"),
                         "delivery": "ready to send - no email is connected"})
    return updated


def update_letter(store: Store, actor: Actor, letter_id: str, *, body_text: str | None = None, approve: bool = False,
                  settings: Settings | None = None) -> dict[str, Any]:
    """Edit the text (re-checked by code; the officer must read it again), or approve = release after sign-off."""
    require_role(actor, "officer")
    letter = one(store.select("letters", eq={"id": letter_id}, limit=1))
    if not letter:
        raise NotFound("Letter not found")
    app = application_for_staff(store, actor, letter["application_id"])
    if letter["status"] == "approved" or letter.get("released_at"):
        raise Conflict("This letter is released and cannot be changed; a change needs a reconsideration and a new version")
    if body_text is None and not approve:
        raise ValidationFailed("Provide body_text to edit, or approve=true")

    if body_text is not None:
        checks = recheck(store, letter, body_text, settings)
        values: dict[str, Any] = {"body_text": body_text, "quality_checks": checks, "reading_grade": checks["reading_grade"],
                                  "checks_passed": checks["all_passed"], "status": "edited", "edited_by": actor.user_id}
        if body_text != letter["body_text"]:
            values |= {"letter_viewed_at": None, "viewed_by": None}       # changed text has to be read again
        updated = store.update("letters", values, eq={"id": letter_id})[0]
        write_audit(store, actor, "letter.edited", application_id=app["id"],
                    details={"letter_id": letter_id, "version": letter["version"], "all_checks_passed": checks["all_passed"]})
        if not approve:
            return updated
        letter = updated

    checks = recheck(store, letter, letter["body_text"], settings)
    if not checks["quotes_match_application"] or not checks["every_reason_cites_confirmed_rule"]:
        raise Conflict("The letter fails a required check and cannot be approved", details={"quality_checks": checks})
    if app["status"] != "signed_off":
        raise Conflict("Sign off the application before approving its letter")
    return release(store, actor, letter, settings)
