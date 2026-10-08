"""Pipeline step 1: load an APPROVED rule pack and its rules.

Refuses draft or retired packs. The returned pack's `version` is stamped on
every assessment run (the database trigger stamps it too).
"""

from __future__ import annotations

import re
from typing import Any

from app.domain import Rule, RulePack
from app.store.base import Store

PLACEHOLDER = re.compile(r"^\[[^\]]+\]$")


class RulePackError(RuntimeError):
    pass


class RulePackNotApproved(RulePackError):
    pass


def is_placeholder(value: Any) -> bool:
    """True for unfilled bracketed placeholders such as "[closing date]"."""
    return isinstance(value, str) and bool(PLACEHOLDER.match(value.strip()))


def load_rule_pack(store: Store, rule_pack_id: str, *, require_approved: bool = True) -> RulePack:
    rows = store.select("rule_packs", eq={"id": rule_pack_id}, limit=1)
    if not rows:
        raise RulePackError(f"rule pack {rule_pack_id} not found")
    pack = rows[0]
    if require_approved and pack["status"] != "approved":
        raise RulePackNotApproved(
            f"rule pack {pack['version']} is {pack['status']}; only approved packs can be used for assessment"
        )
    rule_rows = store.select("rules", eq={"rule_pack_id": rule_pack_id}, order="display_order")
    if not rule_rows:
        raise RulePackError(f"rule pack {pack['version']} has no rules")
    rules = [Rule.model_validate(r) for r in rule_rows]
    return RulePack(
        id=pack["id"],
        grant_program_id=pack["grant_program_id"],
        version=pack["version"],
        status=pack["status"],
        letter_config=pack.get("letter_config") or {},
        rules=sorted(rules, key=lambda r: (r.display_order, r.rule_code)),
    )


def load_active_pack_for_program(store: Store, grant_program_id: str) -> RulePack:
    packs = store.select(
        "rule_packs", eq={"grant_program_id": grant_program_id, "status": "approved"}, order="approved_at", desc=True
    )
    if not packs:
        raise RulePackNotApproved("no approved rule pack for this grant program")
    return load_rule_pack(store, packs[0]["id"])
