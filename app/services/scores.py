"""The weighted total per applicant: plain arithmetic on the officer's marks and the rule pack's weights.

  weighted_total = sum(mark_i * weight_i) / sum(weights)        (marks 0 to 100; weights from the approved rule pack)

Guard rails (the product owner asked for this view; these keep it a tool for people):
- Only the officer's own marks and the pack's weights feed it. No AI output, no finding, no signal.
- Shown only on the Outcomes page and its API, only to people with can_view_round_scores. Never on the Overview, in a letter,
  an email or an AI prompt (letter_checks fails any number that is not from an allowed source, and a test looks for the total).
- The default order is by reference. Sorting by a column is the viewer's explicit click, in the browser. There is no rank number,
  no "top"/"best" wording, no cut-off. "Places available" is a reference line only; nothing is selected by it.
- Exact decimals; the unrounded total is kept, the screen shows one decimal. Missing marks never show 0: the total is "Not yet".
- supabase/migrations/...0021 has the same arithmetic as private.weighted_total; tests keep the two equal.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.access import Actor
from app.services.errors import Forbidden
from app.store.base import Store, one

POLICIES = ("block", "zero", "reweight")
VIEW, RECORD = "can_view_round_scores", "can_record_round_decisions"


def has_permission(store: Store, actor: Actor, permission: str) -> bool:
    """Admins always. An officer whose profile lists permissions needs this one; an officer with no list yet is not restricted."""
    if actor.role == "admin":
        return True
    if actor.role != "officer":
        return False
    perms = (one(store.select("profiles", eq={"id": actor.user_id}, limit=1)) or {}).get("permissions")
    return perms is None or permission in perms


def require(store: Store, actor: Actor, permission: str) -> None:
    if not has_permission(store, actor, permission):
        raise Forbidden("You do not have permission to see round scores. Ask the lead officer or the assessment panel.")


def criteria(store: Store, pack_id: str) -> dict[str, Any]:
    """The merit criteria with their weights, in order. `error` when the weights are missing or not usable."""
    rules = sorted((r for r in store.select("rules", eq={"rule_pack_id": pack_id}) if (r.get("params") or {}).get("section") == "merit"),
                   key=lambda r: (len(r["rule_code"]), r["rule_code"]))
    out, error = [], None
    for r in rules:
        raw = (r.get("params") or {}).get("weight")
        if raw is None:
            w = None          # marked by the officer but not scored (for example "the question is answered")
        else:
            try:
                w = Decimal(str(raw))
                if w <= 0:
                    raise ValueError
            except Exception:
                w, error = Decimal(0), f"{r['rule_code']} has a weight that is not a positive number"
        title = ((r.get("params") or {}).get("title") or r["rule_text"].split(" (")[0].split(".")[0]).strip()
        if len(title) > 40:
            title = title[:38].rsplit(" ", 1)[0] + "…"
        out.append({"code": r["rule_code"], "title": title, "weight": w})
    total = sum((c["weight"] or Decimal(0) for c in out), Decimal(0))
    if not out:
        error = "This rule pack has no merit criteria"
    elif not error and total == 0:
        error = "No merit criterion in this rule pack has a weight"
    return {"items": out, "error": error, "weight_sum": total, "normalised": not error and total != 100}


def policy_of(letter_config: dict[str, Any]) -> str:
    p = (letter_config or {}).get("not_assessed_policy", "block")
    return p if p in POLICIES else "block"


def calculate(crit: dict[str, Any], marks: dict[str, dict[str, Any]], policy: str) -> dict[str, Any]:
    """Pure. `marks`: rule code -> merit_marks row. Returns the status, the unrounded total and each criterion's points."""
    rows, used, points, missing, na = [], Decimal(0), Decimal(0), 0, 0
    for c in crit["items"]:
        m = marks.get(c["code"])
        row = {"code": c["code"], "title": c["title"], "weight": c["weight"], "mark": None, "not_assessed": False, "points": None}
        if m is None:
            missing += 1
        elif c["weight"] is None:
            row["mark"] = None if m.get("not_assessed") else int(m["mark"])   # shown, never scored
            row["not_assessed"] = bool(m.get("not_assessed"))
        elif m.get("not_assessed"):
            na += 1
            row["not_assessed"] = True
            if policy == "zero":
                used += c["weight"]
                row["points"] = Decimal(0)
        else:
            row["mark"] = int(m["mark"])
            used += c["weight"]
            points += Decimal(row["mark"]) * c["weight"]
            row["points"] = Decimal(row["mark"]) * c["weight"]
        rows.append(row)
    n = len(crit["items"])
    base = {"rows": rows, "marked": n - missing, "of": n, "total": None, "policy": policy}
    if crit["error"]:
        return base | {"status": "cannot", "reason": crit["error"]}
    if missing:
        return base | {"status": "waiting", "reason": f"{n - missing} of {n} marked"}
    if na and policy == "block":
        return base | {"status": "cannot", "reason": "A criterion is Not assessed and this round does not calculate a total in that case"}
    if used == 0:
        return base | {"status": "cannot", "reason": "No criterion has a mark"}
    return base | {"status": "complete", "reason": None, "total": points / used, "weight_used": used}


def show(value: Decimal | None, places: int = 1) -> str | None:
    return None if value is None else str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def for_application(store: Store, app: dict[str, Any]) -> dict[str, Any]:
    pack = one(store.select("rule_packs", eq={"id": app["rule_pack_id"]}, limit=1)) or {}
    cfg = pack.get("letter_config") or {}
    crit = criteria(store, app["rule_pack_id"])
    marks = {m["rule_code"]: m for m in store.select("merit_marks", eq={"application_id": app["id"]})}
    return calculate(crit, marks, policy_of(cfg)) | {"criteria": crit, "decimals": int(cfg.get("score_decimals", 1)), "pack_version": pack.get("version")}


def worked_example(res: dict[str, Any]) -> str | None:
    """The formula with this applicant's own numbers, e.g. (82 x 40% + 75 x 30%) / 70% = 79.1."""
    if res["status"] != "complete":
        return None
    parts = [f"{r['mark']} × {r['weight'].normalize():f}%" for r in res["rows"] if r["mark"] is not None and r["weight"]]
    used = res["weight_used"].normalize()
    return f"({' + '.join(parts)}) ÷ {used:f}% = {show(res['total'], res['decimals'])}"


def snapshot(res: dict[str, Any], at: str) -> dict[str, Any]:
    """Stored on the round decision so later changes cannot rewrite history. Strings, so nothing is lost to float rounding."""
    return {"at": at, "rule_pack_version": res["pack_version"], "policy": res["policy"], "status": res["status"],
            "total": None if res["total"] is None else str(res["total"]), "total_shown": show(res["total"], res["decimals"]),
            "criteria": [{"code": r["code"], "weight": None if r["weight"] is None else str(r["weight"]), "mark": r["mark"], "not_assessed": r["not_assessed"]} for r in res["rows"]]}
