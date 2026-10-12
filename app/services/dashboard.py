"""The officer's Overview: where the round is up to and what is waiting for an officer.

A workload and progress view. It is NOT a performance view of applicants, officers or the AI:
- No score, ranking, comparison or sort by merit. Merit shows how many criteria are marked, never a mark.
- Nothing is broken down by officer. "Me" is the officer's own applications (ones they have acted on); "All officers" is the team.
- AI suggestions an officer has not decided are counted as "Suggested", apart from officer decisions ("Confirmed").
- The AI-use check appears only as a count, and only when it is switched on.
- Reading the overview writes nothing.

Single source: every application's place comes from queue.list_queue, which reads steps.state (the same logic as the step bar).
The tiles and stage cards are counts of the SAME rows the queue shows for the matching filter (`filter_rows`), so a tile's
number always equals the number of rows its link opens.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.config import Settings, get_settings
from app.services import ai_use, doc_step, queue, steps as steps_service, substeps as substeps_service
from app.services.access import Actor
from app.services.errors import Forbidden, ValidationFailed
from app.services.review import latest_reviews
from app.store.base import Store

# The eight stages, in the officer's order. 3.2 Consistency and 3.3 AI-use check never block, so an application is never *in*
# those two: their cards count applications in Assessment with a signal open. The other six come from steps.stage_of.
STAGES = ("documents", "redaction", "eligibility", "consistency", "ai_use", "merit", "outcome", "signed_off")
STAGE_TITLES = {"documents": "Documents", "redaction": "Redaction check", "eligibility": "Eligibility", "consistency": "Consistency",
                "ai_use": "AI-use check", "merit": "Merit", "outcome": "Outcome", "signed_off": "Signed off"}

CACHE_SECONDS = 30
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

# Plain reasons for a document that needs asking about (Step 1). Neutral words only.
DOC_REASONS = {"missing": ("not_provided", "Not provided"), "wrong_slot": ("wrong_type", "Wrong document type"),
               "unreadable": ("unreadable", "Unreadable or scanned"), "differs": ("differs", "Details differ from the form"),
               "other": ("asked_again", "Officer asked to see it again")}

# Document kinds for the chart: the Step 1 slots, with both letters of support counted as one kind.
DOC_TYPES = {"coe": "Confirmation of Enrolment", "arrival": "Evidence of arrival date", "letter": "Letter of support",
             "bio": "Biography and headshot", "other": "Other supporting documents"}


def clear_cache() -> None:
    _cache.clear()


# ------------------------------------------------------------------ rows (shared with the queue)

def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _days(since: str | None, now: datetime) -> int | None:
    d = _parse(since)
    return None if d is None else max(0, (now - d).days)


def _mine(store: Store, actor: Actor) -> set[str]:
    """Applications this officer has acted on (from the audit log). The team works one shared queue."""
    return {r["application_id"] for r in store.select("audit_log", eq={"actor_id": actor.user_id}) if r.get("application_id")}


def _doc_issues(store: Store, app: dict[str, Any]) -> list[dict[str, str]]:
    """Step 1 documents still to ask about, by kind and reason. Read only (doc_step.view also refreshes checks; this does not)."""
    docs = store.select("documents", eq={"application_id": app["id"]})
    by_slot, dec = doc_step.assign(docs), doc_step.decisions(store, app["id"])
    need = set(doc_step.required_slots(store, app))
    out = []
    for slot, d in by_slot.items():
        decision = dec.get(slot)
        if decision and decision["decision"] == "not_needed":
            continue
        if decision and decision["decision"] == "confirmed" and d is not None and d["id"] == decision.get("document_id"):
            continue
        issue = doc_step._issue(slot, d, decision, docs)
        if issue and (slot in need or d is not None):
            key, label = DOC_REASONS.get(issue[0], DOC_REASONS["other"])
            kind = "letter" if slot.startswith("letter") else "other" if slot.startswith("other") else slot
            out.append({"slot": kind, "type": DOC_TYPES[kind], "reason": key, "reason_label": label})
    return out


def rows(store: Store, actor: Actor, settings: Settings | None = None, *, rule_pack: str | None = None, officer: str = "all",
         date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """The queue's rows (same access rules), with the overview's facts added. Filters narrow the set; they never reorder it."""
    return narrow(store, actor, with_facts(store, actor, settings), rule_pack=rule_pack, officer=officer, date_from=date_from, date_to=date_to)


def narrow(store: Store, actor: Actor, out: list[dict[str, Any]], *, rule_pack: str | None = None, officer: str = "all",
           date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    if rule_pack:
        out = [r for r in out if r["rule_pack_id"] == rule_pack]
    if officer == "me":
        mine = _mine(store, actor)
        out = [r for r in out if r["id"] in mine]
    if date_from:
        out = [r for r in out if (r.get("submitted_at") or "")[:10] >= date_from]
    if date_to:
        out = [r for r in out if (r.get("submitted_at") or "")[:10] <= date_to]
    return out


def with_facts(store: Store, actor: Actor, settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    out = queue.list_queue(store, actor, settings)
    if not out:
        return out
    apps = {a["id"]: a for a in store.select("applications", in_={"id": [r["id"] for r in out]})}
    for r in out:
        app = apps[r["id"]]
        st = steps_service.state(store, app, settings)
        step3 = st["steps"][2]
        subs = {s["key"]: s for s in step3.get("substeps") or []}
        recorded = steps_service.rows(store, app["id"])
        r["rule_pack_id"] = app.get("rule_pack_id")
        r["waiting"] = app["status"] == "awaiting_applicant"
        # Nobody has started it: no step recorded and no AI check run.
        r["not_started"] = app["status"] != "signed_off" and not recorded and r["attention"]["not_yet_assessed"]
        r["in_assessment"] = r["stage"] in ("eligibility", "merit")
        r["signals"] = {
            "consistency": subs.get("consistency", {}).get("count", 0) if r["in_assessment"] else 0,
            "ai_use": subs.get("ai_use", {}).get("count", 0) if r["in_assessment"] else 0,
        }
        r["ai_use_shown"] = "ai_use" in subs
        r["doc_issues"] = _doc_issues(store, app) if r["stage"] == "documents" else []
        r["pending"] = {"eligibility": subs.get("eligibility", {}).get("count", 0), "merit": subs.get("merit", {}).get("count", 0),
                        "documents": len(st["steps"][0]["missing"]) if st["steps"][0]["status"] != "done" else 0}
        r["has_run"] = not r["attention"]["not_yet_assessed"] or r["attention"]["manual_assessment_requested"]
        # When it last moved (received, or a step changed): how long it has been waiting.
        moved = [app.get("submitted_at")] + [x.get("updated_at") or x.get("created_at") for x in recorded.values()]
        r["waiting_since_any"] = max((m for m in moved if m), default=None)
        r["deep_link"] = _deep_link(r)
        r["needed"] = _needed(r)
    return out


def _deep_link(r: dict[str, Any]) -> str:
    step = {"documents": 1, "redaction": 2, "eligibility": 3, "merit": 3, "outcome": 4, "signed_off": 4}[r["stage"]]
    sub = {"eligibility": "eligibility", "merit": "merit"}.get(r["stage"])
    return f"#/applications/{r['id']}?step={step}" + (f"&sub={sub}" if sub else "")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _needed(r: dict[str, Any]) -> str:
    """What the officer needs to do, in plain words."""
    s, p = r["stage"], r["pending"]
    if s == "signed_off":
        return "Signed off"
    if r["waiting"]:
        return "Waiting for the applicant to reply"
    if s == "documents":
        return _plural(p["documents"], "document to confirm", "documents to confirm") if p["documents"] else "Documents to check"
    if s == "redaction":
        return "Redaction not approved"
    if s == "eligibility":
        if not r["has_run"]:
            return "AI check not run yet"
        return _plural(p["eligibility"], "finding needs a decision", "findings need a decision")
    if s == "merit":
        return _plural(p["merit"], "merit criterion to mark", "merit criteria to mark")
    return "Ready for sign-off"


# ------------------------------------------------------------------ filters (shared with the queue)

def _stage_filter(stage: str) -> Callable[[dict[str, Any]], bool]:
    if stage == "consistency":
        return lambda r: r["signals"]["consistency"] > 0
    if stage == "ai_use":
        return lambda r: r["signals"]["ai_use"] > 0
    return lambda r: r["stage"] == stage


FILTERS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "received": lambda r: True,
    "need_action": lambda r: r["stage"] != "signed_off" and not r["waiting"],
    "waiting_applicant": lambda r: r["waiting"],
    "ready_signoff": lambda r: r["stage"] == "outcome" and not r["waiting"],
    "signed_off": lambda r: r["stage"] == "signed_off",
    "not_started": lambda r: r["not_started"],
    **{f"stage:{s}": _stage_filter(s) for s in STAGES},
    "signal:consistency": lambda r: r["signals"]["consistency"] > 0,
    "signal:ai_use": lambda r: r["signals"]["ai_use"] > 0,
}

def matches(row: dict[str, Any], key: str) -> bool:
    if key.startswith("docs:"):
        _, slot, reason = (key.split(":") + ["", ""])[:3]
        return any(i["slot"] == slot and (not reason or i["reason"] == reason) for i in row["doc_issues"])
    if key not in FILTERS:
        raise ValidationFailed("Unknown filter")
    return FILTERS[key](row)


def filter_rows(all_rows: list[dict[str, Any]], key: str | None) -> list[dict[str, Any]]:
    return all_rows if not key else [r for r in all_rows if matches(r, key)]


# ------------------------------------------------------------------ the overview

def _round(store: Store, packs: list[dict[str, Any]], pack_id: str | None, now: datetime) -> dict[str, Any] | None:
    pack = next((p for p in packs if p["id"] == pack_id), None)
    if not pack:
        return None
    program = (store.select("grant_programs", eq={"id": pack["grant_program_id"]}, limit=1) or [{}])[0]
    cfg = (pack.get("letter_config") or {}).get("round") or {}
    out: dict[str, Any] = {"id": pack["id"], "rule_pack_version": pack.get("version"), "program": program.get("name"),
                           "name": cfg.get("name"), "opens_at": cfg.get("opens_at"), "closes_at": cfg.get("closes_at"),
                           "decisions_due": cfg.get("decisions_due")}
    opens, closes = _parse(cfg.get("opens_at")), _parse(cfg.get("closes_at"))
    if closes:
        out["days_left"] = (closes.date() - now.date()).days
        out["closed"] = out["days_left"] < 0
    if opens and closes and closes > opens:
        out["elapsed"] = round(min(1.0, max(0.0, (now - opens) / (closes - opens))), 3)
    return out


def _findings(store: Store, ids: list[str]) -> dict[str, Any]:
    """Eligibility only (merit has its own card). Officer decisions and AI suggestions are counted apart."""
    runs: dict[str, dict[str, Any]] = {}
    for r in store.select("assessment_runs", in_={"application_id": ids}) if ids else []:
        if r["status"] == "complete" and (r["application_id"] not in runs or (r.get("finished_at") or "") > (runs[r["application_id"]].get("finished_at") or "")):
            runs[r["application_id"]] = r
    run_ids = [r["id"] for r in runs.values()]
    findings = store.select("findings", in_={"run_id": run_ids}) if run_ids else []
    rules = {r["id"]: r for r in store.select("rules", in_={"id": list({f["rule_id"] for f in findings})})} if findings else {}
    findings = [f for f in findings if (rules.get(f["rule_id"], {}).get("params") or {}).get("section") != "merit"]
    reviews = latest_reviews(store, [f["id"] for f in findings])
    by_status = {"met": 0, "not_met": 0, "needs_evidence": 0, "unclear": 0, "officer_judgement": 0}
    key = {"Met": "met", "Not met": "not_met", "Needs evidence": "needs_evidence", "Unclear": "unclear"}
    suggested = 0
    per_app: dict[str, list[str | None]] = {}
    for f in findings:
        rv = reviews.get(f["id"])
        if rv is None:
            if f.get("check_source") == "human_only" or f["ai_status"] == "Evidence only":
                by_status["officer_judgement"] += 1     # no AI suggestion: waiting for the officer's own judgement
            else:
                suggested += 1                          # AI or code proposed it; no officer decision yet
            per_app.setdefault(f["application_id"], []).append(None)
            continue
        status = "Needs evidence" if rv["action"] == "ask_applicant" else rv["final_status"]
        by_status[key[status]] += 1
        per_app.setdefault(f["application_id"], []).append(None if rv["action"] == "ask_applicant" else status)
    return {
        "confirmed": {"not_met_apps": sum("Not met" in v for v in per_app.values()),
                      "all_met_apps": sum(bool(v) and all(s == "Met" for s in v) for v in per_app.values())},
        "suggested_pending": suggested,
        "by_status": by_status,
        "_findings": findings, "_rules": rules, "_reviews": reviews,
    }


def _merit_progress(store: Store, all_rows: list[dict[str, Any]], pack_ids: set[str]) -> list[dict[str, Any]]:
    """Per criterion: how many applications have it marked (or Not assessed), out of those assessed. Counts only, never a mark."""
    rules = sorted([r for r in store.select("rules", in_={"rule_pack_id": list(pack_ids)}) if (r.get("params") or {}).get("section") == "merit"],
                   key=lambda r: (r.get("display_order", 0), r["rule_code"])) if pack_ids else []
    assessed = [r["id"] for r in all_rows if r["has_run"]]
    marked: dict[str, set[str]] = {}
    for m in store.select("merit_marks", in_={"application_id": assessed}) if assessed else []:
        marked.setdefault(m["rule_code"], set()).add(m["application_id"])   # only WHICH criteria are marked; the value is never read
    seen, out = set(), []
    for r in rules:
        if r["rule_code"] in seen:
            continue
        seen.add(r["rule_code"])
        total = [a["id"] for a in all_rows if a["has_run"] and a.get("rule_pack_id") == r["rule_pack_id"]]
        out.append({"criterion": r["rule_code"], "title": (r.get("params") or {}).get("title") or r["rule_text"][:60],
                    "marked": len(marked.get(r["rule_code"], set()) & set(total)), "total": len(total)})
    return out


def _outcomes(store: Store, all_rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    ids = [r["id"] for r in all_rows]
    signed = {r["id"] for r in all_rows if r["stage"] == "signed_off"}
    letters = store.select("letters", in_={"application_id": ids}) if ids else []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for l in letters:
        k = (l["application_id"], l.get("kind", "decline"))
        if k not in latest or l["version"] > latest[k]["version"]:
            latest[k] = l
    requests = store.select("clarification_requests", in_={"application_id": ids}) if ids else []
    sign_offs = store.select("sign_offs", in_={"application_id": list(signed)}) if signed else []
    received = {r["id"]: r.get("submitted_at") for r in all_rows}
    durations = []
    per_day: dict[str, int] = {}
    for s in sign_offs:
        a, b = _parse(received.get(s["application_id"])), _parse(s.get("signed_at"))
        if a and b:
            durations.append((b - a).total_seconds() / 86400)
        if b:
            per_day[b.date().isoformat()] = per_day.get(b.date().isoformat(), 0) + 1
    return {
        "declines_drafted": sum(1 for (a, k), l in latest.items() if k == "decline" and a not in signed),
        "next_steps_drafted": sum(1 for (a, k), l in latest.items() if k == "next_steps" and a not in signed),
        "requests_drafted": sum(1 for r in requests if r.get("status") == "draft"),
        "released": len({a for (a, k), l in latest.items() if a in signed and l["status"] == "approved"}),
        # A process measure (how long the work takes), not a measure of any applicant or officer.
        "avg_days_to_signoff": round(sum(durations) / len(durations), 1) if durations else None,
        "signed_off_per_day": [{"date": d, "count": per_day[d]} for d in sorted(per_day)],
    }


def _trust(store: Store, all_rows: list[dict[str, Any]], f: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Is the tool behaving? Counts only, aggregated by rule, never by person."""
    findings, rules, reviews = f["_findings"], f["_rules"], f["_reviews"]
    overrides: dict[str, int] = {}
    for x in findings:
        rv = reviews.get(x["id"])
        if rv and rv["action"] == "override" and rv["final_status"] != x["ai_status"]:
            code = rules.get(x["rule_id"], {}).get("rule_code", "?")
            overrides[code] = overrides.get(code, 0) + 1
    ids = [r["id"] for r in all_rows]
    quoted = [x for x in findings if x.get("evidence_quote")]
    leak_stops = sum(1 for r in store.select("redaction_runs", in_={"application_id": ids}) if r.get("status") == "blocked_redaction_leak") if ids else 0
    return {
        "overrides_by_rule": [{"rule": k, "count": v} for k, v in sorted(overrides.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
        "quotes_verified": sum(bool(x.get("quote_verified")) for x in quoted),
        "quotes_not_found": sum(not x.get("quote_verified") for x in quoted),
        "ai_failures": sum(bool(x.get("error_flag")) for x in findings),
        "redaction_approved": sum(1 for r in all_rows if r["stage"] not in ("documents", "redaction")),
        "leak_stops": leak_stops,
        "language_flags": sum(bool(x.get("language_flag")) for x in findings),
        "provider": {"name": settings.llm_provider, "offline": settings.llm_provider == "stub", "demo": settings.app_mode == "demo"},
    }


def overview(store: Store, actor: Actor, settings: Settings | None = None, *, rule_pack: str | None = None, officer: str = "all",
             date_from: str | None = None, date_to: str | None = None, refresh: bool = False) -> dict[str, Any]:
    if not actor.is_staff:
        raise Forbidden("Officers only")
    if officer not in ("me", "all"):
        raise ValidationFailed("officer must be me or all")
    settings = settings or get_settings()
    key = (actor.user_id, rule_pack, officer, date_from, date_to)
    hit = _cache.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = _build(store, actor, settings, rule_pack=rule_pack, officer=officer, date_from=date_from, date_to=date_to)
    _cache[key] = (time.monotonic(), data)
    return data


def _build(store: Store, actor: Actor, settings: Settings, *, rule_pack: str | None, officer: str, date_from: str | None,
           date_to: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    everyone = with_facts(store, actor, settings)
    # The rule packs this officer can see applications for (the switcher), by how many applications they hold.
    pack_count: dict[str, int] = {}
    for r in everyone:
        pack_count[r["rule_pack_id"]] = pack_count.get(r["rule_pack_id"], 0) + 1
    packs = store.select("rule_packs", in_={"id": list(pack_count)}) if pack_count else []
    programs = {p["id"]: p for p in store.select("grant_programs", in_={"id": list({p["grant_program_id"] for p in packs})})} if packs else {}
    pack_id = rule_pack or (max(pack_count, key=lambda k: (pack_count[k], k)) if pack_count else None)
    all_rows = narrow(store, actor, everyone, rule_pack=pack_id, officer=officer, date_from=date_from, date_to=date_to) if pack_id else []
    count = lambda k: len(filter_rows(all_rows, k))  # noqa: E731 - the same filter the queue applies

    ai_on = any(r["ai_use_shown"] for r in all_rows) if all_rows else False
    apps = {a["id"]: a for a in store.select("applications", in_={"id": [r["id"] for r in all_rows]})} if all_rows else {}
    preview = ai_on and all(ai_use.active(store, apps[r["id"]])[1] for r in all_rows if r["ai_use_shown"])

    pipeline = []
    for s in STAGES:
        here = filter_rows(all_rows, f"stage:{s}")
        enabled = ai_on if s == "ai_use" else True
        waiting = sum(r["waiting"] for r in here)
        pipeline.append({"stage": s, "title": STAGE_TITLES[s], "count": len(here) if enabled else 0,
                         "needs_you": (len(here) - waiting) if enabled and s != "signed_off" else 0,
                         "waiting_applicant": waiting if enabled else 0, "enabled": enabled,
                         "never_blocks": s in substeps_service.OPTIONAL, "preview": preview if s == "ai_use" else False})

    # Needs attention: waiting on an officer, the longest wait first (then the oldest received). Never by merit.
    need = sorted(filter_rows(all_rows, "need_action"), key=lambda r: (r["waiting_since_any"] or "", r.get("submitted_at") or ""))
    attention = [{"application_id": r["id"], "ref": r["reference"], "applicant_name": r["applicant_name"], "stage": r["stage"],
                  "stage_title": STAGE_TITLES[r["stage"]], "needed": r["needed"],
                  "waiting_days": _days(r["waiting_since_any"], now), "deep_link": r["deep_link"]} for r in need]

    docs: dict[tuple[str, str], dict[str, Any]] = {}
    for r in all_rows:
        for i in r["doc_issues"]:
            d = docs.setdefault((i["slot"], i["reason"]), {"slot": i["slot"], "type": i["type"], "reason": i["reason"],
                                                         "reason_label": i["reason_label"], "count": 0, "applications": []})
            d["count"] += 1
            d["applications"].append({"application_id": r["id"], "ref": r["reference"]})

    waiting_rows = filter_rows(all_rows, "waiting_applicant")
    waits = sorted([{"application_id": r["id"], "ref": r["reference"], "days": _days(r.get("waiting_since") or r["waiting_since_any"], now),
                     "deep_link": r["deep_link"]} for r in waiting_rows], key=lambda w: -(w["days"] or 0))

    f = _findings(store, [r["id"] for r in all_rows])
    trust = _trust(store, all_rows, f, settings)
    signals: dict[str, Any] = {"consistency_pending": count("signal:consistency"), "ai_use_enabled": ai_on}
    if ai_on:
        signals["ai_use_pending"] = count("signal:ai_use")
        signals["ai_use_preview"] = preview
    pack = next((p for p in packs if p["id"] == pack_id), None)
    return {
        "generated_at": now.isoformat(),
        "cache_seconds": CACHE_SECONDS,
        "officer": officer,
        "round": _round(store, packs, pack_id, now),
        "rule_packs": [{"id": p["id"], "label": f"{programs.get(p['grant_program_id'], {}).get('name', '')} · {p.get('version')}",
                        "applications": pack_count[p["id"]]} for p in sorted(packs, key=lambda p: p["id"] != pack_id)],
        "tiles": {k: count(k) for k in ("received", "need_action", "waiting_applicant", "ready_signoff", "signed_off", "not_started")},
        "pipeline": pipeline,
        "attention": attention[:8],
        "attention_total": len(attention),
        "next_deadline": (pack.get("letter_config") or {}).get("round", {}).get("decisions_due") if pack else None,
        "documents_missing": sorted(docs.values(), key=lambda d: (-d["count"], d["type"], d["reason"])),
        "waiting": {"count": len(waiting_rows), "oldest_days": waits[0]["days"] if waits else None, "longest": waits[:5]},
        "findings": {k: v for k, v in f.items() if not k.startswith("_")},
        "signals": signals,
        "merit_progress": _merit_progress(store, all_rows, {pack_id} if pack_id else set()),
        "outcomes": _outcomes(store, all_rows, now),
        "trust": trust,
    }
