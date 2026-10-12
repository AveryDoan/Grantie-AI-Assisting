"""The fairness gate for the AI-use check. The check may be on only if the second-language flag rate is at most 2 points
above polished English, and only if that was measured on real human writing. Fails if the stored result breaks the rule."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from eval import ai_use_check as gate_eval

ROOT = Path(__file__).resolve().parents[1]


def test_threshold_flags_at_most_the_allowed_share_of_every_group():
    groups = {"a": [0.1] * 48 + [0.97, 0.99], "b": [0.2] * 50, "c": [0.3] * 47 + [0.95, 0.96, 0.995]}
    t = gate_eval.lowest_threshold(groups, 0.02)        # one text in 50 may be flagged
    for scores in groups.values():
        assert sum(s >= t for s in scores) / len(scores) <= 0.02
    assert t <= 0.990001 and t > 0.97                    # the lowest threshold that does it, not a higher one


def test_the_gate_turns_the_check_off_when_second_language_writing_is_flagged_more():
    assert gate_eval.gate({"a": 0.05, "c": 0.07}, True)["enabled"] is True
    assert gate_eval.gate({"a": 0.05, "c": 0.0701}, True)["enabled"] is False
    assert gate_eval.gate({"a": 0.0, "c": 0.0}, False)["enabled"] is False     # synthetic "human" text cannot switch it on


def test_the_stored_result_respects_the_second_language_limit():
    """Fails if the check is switched on while the second-language flag rate exceeds the limit."""
    cfg = yaml.safe_load((ROOT / "config" / "ai_use_check.yaml").read_text(encoding="utf-8"))
    report = ROOT / "eval" / "reports" / "ai_use_check.json"
    if not report.exists():
        assert cfg.get("enabled") is False, "the check cannot be on without a fairness report"
        return
    r = json.loads(report.read_text(encoding="utf-8"))
    assert cfg["enabled"] == r["gate"]["enabled"], "config/ai_use_check.yaml must follow the gate"
    gap = r["flag_rate"]["c"] - r["flag_rate"]["a"]
    if cfg["enabled"]:
        assert gap <= 0.02 + 1e-9, f"second-language flag rate is {gap:.1%} above polished English"
        assert r["gate"]["human_provenance"], "the gate was not measured on human writing"
    assert cfg["thresholds"] == r["thresholds"]


def test_the_seeded_pack_follows_the_gate():
    from seed import data

    cfg = yaml.safe_load((ROOT / "config" / "ai_use_check.yaml").read_text(encoding="utf-8"))
    assert data.SNT_AI_USE["ai_check"]["enabled"] == bool(cfg.get("enabled"))
