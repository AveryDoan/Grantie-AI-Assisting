"""Reference lists (official lookup lists that rules read), entered and updated by officers.

- `items` (plain names) is what the rules read; `entries` keeps the richer rows (code, skill level, tier) for display.
- Parsing is code, not AI. Anything it cannot read is reported as a warning, never guessed.
- A preview never saves. Saving replaces the whole list and is audited with counts only.
- Changing a list changes the assessment input hash, so re-assessing creates a fresh run.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from app.services.access import Actor, require_role
from app.services.audit import write_audit
from app.services.errors import NotFound, ValidationFailed
from app.store.base import Store, now_iso, one

MAX_TEXT = 400_000
MAX_ENTRIES = 5000

SOPL = "nt_skilled_occupation_priority_list"
TIER_HEADINGS = {"high priority occupations": "High priority", "priority occupations": "Priority"}

_ROW = re.compile(r"^(?P<code>\d{6})\s+(?P<name>.+?)\s+(?P<level>[1-5])\s*$")
_NOISE = re.compile(r"^(?:\d{4} northern territory|department of|\d{1,2} \w+ \d{4}\s*\|\s*page|page \d+)", re.I)


def parse_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows of `OSCA code, occupation, skill level` (headers, footers and page furniture are skipped).

    Falls back to one name per line, or a CSV, when no coded rows are found."""
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    tier = ""
    skipped = 0
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        key = line.lower().rstrip(".")
        if key in TIER_HEADINGS:
            tier = TIER_HEADINGS[key]
            continue
        m = _ROW.match(line)
        if m:
            entries.append({"code": m["code"], "name": m["name"].strip(), "skill_level": int(m["level"]), "tier": tier})
        elif re.match(r"^\d{6}\b", line):
            skipped += 1
            warnings.append(f"Could not read this row: {line[:80]}")
        elif not _NOISE.match(line):
            continue
    if entries:
        return _dedupe(entries, warnings), warnings[:20]
    return _plain(text, warnings)


def _plain(text: str, warnings: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    names: list[str] = []
    rows = list(csv.reader(io.StringIO(text))) if "," in text.splitlines()[0:1].__str__() and text.count("\n") else None
    source = [r[0] for r in rows if r] if rows else text.splitlines()
    for line in source:
        name = re.sub(r"\s+", " ", line).strip(" -•*\t\"")
        if len(name) >= 2 and re.search(r"[A-Za-z]{2}", name):
            names.append(name)
    entries = [{"code": "", "name": n, "skill_level": None, "tier": ""} for n in names]
    return _dedupe(entries, warnings), warnings[:20]


def _dedupe(entries: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out = []
    for e in entries:
        k = (e["code"], e["name"].lower())
        if k in seen:
            warnings.append(f"Repeated entry ignored: {e['code']} {e['name']}".strip())
            continue
        seen.add(k)
        out.append(e)
    return out


def items_of(entries: list[dict[str, Any]]) -> list[str]:
    """Names the rules match against (unique, original order)."""
    seen: set[str] = set()
    out = []
    for e in entries:
        k = e["name"].lower()
        if k not in seen:
            seen.add(k)
            out.append(e["name"])
    return out


def _row(store: Store, name: str) -> dict[str, Any]:
    row = one(store.select("reference_lists", eq={"name": name}, limit=1))
    if not row:
        raise NotFound("Reference list not found")
    return row


def _rules_using(store: Store, name: str) -> list[str]:
    ids: list[str] = []
    for rule in store.select("rules"):
        if (rule.get("params") or {}).get("list") == name:
            ids.append(rule.get("rule_code"))
    return sorted(i for i in ids if i)


def _summary(store: Store, row: dict[str, Any]) -> dict[str, Any]:
    entries = row.get("entries") or []
    tiers: dict[str, int] = {}
    for e in entries:
        if e.get("tier"):
            tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    items = row.get("items") or []
    return {
        "name": row["name"], "description": row.get("description"), "source": row.get("source"),
        "edition": row.get("edition"), "count": len(items), "loaded": bool(items), "tiers": tiers,
        "updated_at": row.get("updated_at"), "used_by": _rules_using(store, row["name"]),
    }


def list_all(store: Store, actor: Actor) -> list[dict[str, Any]]:
    require_role(actor, "officer", "admin")
    return [_summary(store, r) for r in sorted(store.select("reference_lists"), key=lambda r: r["name"])]


def get_one(store: Store, actor: Actor, name: str, q: str | None = None) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    row = _row(store, name)
    entries = row.get("entries") or [{"code": "", "name": n, "skill_level": None, "tier": ""} for n in row.get("items") or []]
    if q:
        needle = q.strip().lower()
        entries = [e for e in entries if needle in e["name"].lower() or needle in (e.get("code") or "")]
    return {**_summary(store, row), "entries": entries[:1000], "entries_truncated": len(entries) > 1000}


def _diff(row: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    old = {n.lower(): n for n in row.get("items") or []}
    new = {n.lower(): n for n in items_of(entries)}
    return {
        "added": sorted(new[k] for k in new.keys() - old.keys())[:50],
        "removed": sorted(old[k] for k in old.keys() - new.keys())[:50],
        "added_count": len(new.keys() - old.keys()), "removed_count": len(old.keys() - new.keys()),
    }


def _checked(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValidationFailed("The list is too large or not text.")
    entries, warnings = parse_text(text)
    if not entries:
        raise ValidationFailed("No entries were found. Paste one row per line: code, occupation, skill level.")
    if len(entries) > MAX_ENTRIES:
        raise ValidationFailed(f"More than {MAX_ENTRIES} entries; check the file.")
    return entries, warnings


def preview(store: Store, actor: Actor, name: str, text: str) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    row = _row(store, name)
    entries, warnings = _checked(text)
    tiers: dict[str, int] = {}
    for e in entries:
        if e["tier"]:
            tiers[e["tier"]] = tiers.get(e["tier"], 0) + 1
    return {"count": len(entries), "tiers": tiers, "warnings": warnings, "sample": entries[:10],
            "current_count": len(row.get("items") or []), **_diff(row, entries)}


def save(store: Store, actor: Actor, name: str, text: str, edition: str | None, source: str | None) -> dict[str, Any]:
    require_role(actor, "officer", "admin")
    row = _row(store, name)
    entries, warnings = _checked(text)
    diff = _diff(row, entries)
    values = {
        "items": items_of(entries), "entries": entries, "edition": (edition or "").strip()[:80] or None,
        "source": (source or "").strip()[:300] or row.get("source"), "loaded_by": actor.user_id, "updated_at": now_iso(),
    }
    store.update("reference_lists", values, eq={"name": name})
    write_audit(store, actor, "reference_list_saved", details={
        "list": name, "count": len(values["items"]), "added": diff["added_count"], "removed": diff["removed_count"],
        "warnings": len(warnings)})
    return {**_summary(store, _row(store, name)), "warnings": warnings}
