"""Evaluation harness.

Runs the (pure) assessment pipeline on every evaluation case and compares
the AI's suggested status with the human-written answer key. Reports:
- accuracy against the answer key (overall, by rule type, by language style);
- quote validity rate (quotes found in the source text by code);
- twin consistency: the same family_id (identical facts, different writing
  style) should get the same status for every rule;
- the injection test outcome (flagged, and not obeyed);
- a list of every failure.

Evaluation never changes application status and never creates assessment
runs; it only writes evaluation_runs / evaluation_results.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.llm import LLMClient
from app.pipeline.orchestrator import assess_application, load_reference_lists
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.rules_loader import RulePackNotApproved, load_rule_pack
from app.store.base import Store, one


@dataclass
class CaseResult:
    case_code: str
    family_id: str | None
    style: str | None
    rule_code: str
    rule_type: str
    rule_id: str
    expected: str
    predicted: str | None
    correct: bool
    quote_valid: bool | None
    language_flag: bool
    error: bool
    valid: bool = True
    twin_consistent: bool | None = None
    case_id: str = ""


@dataclass
class EvalReport:
    started_at: str
    finished_at: str
    model_name: str
    provider_label: str
    results: list[CaseResult] = field(default_factory=list)
    injection: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    llm_stats: dict[str, int] = field(default_factory=dict)

    # ---- metrics ----------------------------------------------------------
    @staticmethod
    def _rate(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    def summary(self) -> dict[str, Any]:
        rs = self.results
        quoted = [r for r in rs if r.quote_valid is not None]
        twins = [r for r in rs if r.twin_consistent is not None]
        by_type: dict[str, list[CaseResult]] = defaultdict(list)
        by_style: dict[str, list[CaseResult]] = defaultdict(list)
        for r in rs:
            by_type[r.rule_type].append(r)
            by_style[r.style or "untagged"].append(r)
        families = sorted({r.family_id for r in rs if r.family_id})
        return {
            "cases": len({r.case_code for r in rs}),
            "rule_checks": len(rs),
            "accuracy": self._rate(sum(r.correct for r in rs), len(rs)),
            "accuracy_by_rule_type": {k: self._rate(sum(r.correct for r in v), len(v)) for k, v in sorted(by_type.items())},
            "accuracy_by_language_style": {k: self._rate(sum(r.correct for r in v), len(v)) for k, v in sorted(by_style.items())},
            "quote_validity_rate": self._rate(sum(bool(r.quote_valid) for r in quoted), len(quoted)),
            "quotes_checked": len(quoted),
            "twin_consistency_rate": self._rate(sum(bool(r.twin_consistent) for r in twins), len(twins)),
            "twin_families": families,
            "twin_groups": [
                {"family_id": f, "rule_code": rc, "consistent": bool(ms[0].twin_consistent),
                 "members": [{"case_code": m.case_code, "style": m.style, "predicted": m.predicted} for m in ms]}
                for (f, rc), ms in sorted(self._families().items())
            ],
            "language_flags": sum(r.language_flag for r in rs),
            "invalid_findings": sum(not r.valid for r in rs),
            "errors": sum(r.error for r in rs),
            "injection_tests": self.injection,
            "failures": len(self.failures()),
            "skipped": self.skipped,
            "llm_stats": self.llm_stats,
        }

    def _families(self) -> dict[tuple[str, str], list[CaseResult]]:
        groups: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
        for r in self.results:
            if r.family_id:
                groups[(r.family_id, r.rule_code)].append(r)
        return groups

    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.correct or r.quote_valid is False or r.twin_consistent is False]

    # ---- markdown ---------------------------------------------------------
    def markdown(self) -> str:
        s = self.summary()
        pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
        lines = [
            "# Evaluation report",
            "",
            f"- Run: {self.started_at} to {self.finished_at}",
            f"- Provider / model: **{self.provider_label}** (`{self.model_name}`), prompt version `{PROMPT_VERSION}`",
            "- Data: synthetic evaluation cases only (all fictional).",
            "- Answer key: written from the rule text and case facts, never from model output. "
            "The seeded key is marked REVIEW REQUIRED until a human officer has checked it (answer_key.written_by).",
        ]
        if "stub" in self.provider_label.lower():
            lines += [
                "",
                "> **This report was produced with the offline keyword stub, NOT a language model.** "
                "It demonstrates that the harness works end to end. Its accuracy says nothing about a real "
                "model. Re-run with `python -m eval.run` and a GEMINI_API_KEY for real figures.",
            ]
        lines += [
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Cases | {s['cases']} |",
            f"| Rule checks | {s['rule_checks']} |",
            f"| Accuracy vs answer key | {pct(s['accuracy'])} |",
            f"| Quote validity rate | {pct(s['quote_validity_rate'])} ({s['quotes_checked']} quoted findings) |",
            f"| Twin consistency (same facts, different writing style) | {pct(s['twin_consistency_rate'])} |",
            f"| Language flags raised | {s['language_flags']} |",
            f"| Findings with errors | {s['errors']} |",
            f"| Findings marked invalid by verification | {s['invalid_findings']} |",
            f"| Failures listed below | {s['failures']} |",
            "",
            "### Accuracy by rule type",
            "",
            "| Rule type | Accuracy |",
            "|---|---|",
            *[f"| {k} | {pct(v)} |" for k, v in s["accuracy_by_rule_type"].items()],
            "",
            "### Accuracy by language style",
            "",
            "Large gaps between styles would mean the system treats people differently because of how they write.",
            "",
            "| Style | Accuracy |",
            "|---|---|",
            *[f"| {k} | {pct(v)} |" for k, v in s["accuracy_by_language_style"].items()],
            "",
            "## Twin consistency by family",
            "",
            "| Family | Rule | Statuses (case: status) | Consistent |",
            "|---|---|---|---|",
        ]
        fam: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
        for r in self.results:
            if r.family_id:
                fam[(r.family_id, r.rule_code)].append(r)
        for (f, rule), rs in sorted(fam.items()):
            statuses = ", ".join(f"{r.case_code} ({r.style}): {r.predicted}" for r in rs)
            lines.append(f"| {f} | {rule} | {statuses} | {'yes' if rs[0].twin_consistent else '**no**'} |")
        lines += ["", "## Injection tests", ""]
        if not self.injection:
            lines.append("No injection cases in this evaluation set.")
        for inj in self.injection:
            lines.append(
                f"- **{inj['case_code']}**: flagged = {'yes' if inj['flagged'] else '**NO**'} "
                f"({', '.join(inj['patterns']) or 'none'}); "
                f"instructions obeyed = {'**YES**' if inj['obeyed'] else 'no'} "
                f"(rules predicted Met that the answer key does not expect: {', '.join(inj['unexpected_met']) or 'none'})"
            )
        lines += ["", "## Every failure", ""]
        fails = self.failures()
        if not fails:
            lines.append("None.")
        else:
            lines += ["| Case | Style | Rule | Expected | Predicted | Quote valid | Twin consistent |", "|---|---|---|---|---|---|---|"]
            for r in fails:
                lines.append(
                    f"| {r.case_code} | {r.style or '-'} | {r.rule_code} | {r.expected} | {r.predicted} | "
                    f"{'-' if r.quote_valid is None else r.quote_valid} | {'-' if r.twin_consistent is None else r.twin_consistent} |"
                )
        if self.skipped:
            lines += ["", "## Skipped cases", ""] + [f"- {x['case_code']}: {x['reason']}" for x in self.skipped]
        lines += [
            "",
            "## How to read this",
            "",
            "- The system never decides eligibility. These figures measure how often its *suggestions* match a human answer key.",
            "- \"Evidence only\" is the expected output for judgement rules: no status is ever suggested for them.",
            "- A wrong suggestion is caught by the officer review step; a high failure rate means more officer work, not wrong decisions.",
        ]
        return "\n".join(lines) + "\n"


def run_evaluation(
    store: Store, llm: LLMClient | None, settings: Settings, *, provider_label: str, write_db: bool = True
) -> EvalReport:
    started = datetime.now(timezone.utc).isoformat()
    report = EvalReport(started_at=started, finished_at=started, model_name=llm.model_name if llm else "none",
                        provider_label=provider_label)
    cases = sorted(store.select("evaluation_cases"), key=lambda c: c["case_code"])
    packs: dict[str, Any] = {}

    for case in cases:
        app = one(store.select("applications", eq={"id": case["application_id"]}, limit=1))
        if not app:
            report.skipped.append({"case_code": case["case_code"], "reason": "application missing"})
            continue
        if app.get("manual_assessment_requested"):
            report.skipped.append({"case_code": case["case_code"], "reason": "manual assessment requested"})
            continue
        try:
            pack = packs.get(app["rule_pack_id"]) or load_rule_pack(store, app["rule_pack_id"])
        except RulePackNotApproved:
            report.skipped.append({"case_code": case["case_code"], "reason": "rule pack retired (superseded by a newer version)"})
            continue
        packs[app["rule_pack_id"]] = pack
        outcome = assess_application(
            application=app,
            documents=store.select("documents", eq={"application_id": app["id"]}),
            pack=pack,
            register_rows=store.select("mock_grants_register", eq={"applicant_id": app["applicant_id"]}),
            llm=llm,
            settings=settings,
            applicant=one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)),
            reference_lists=load_reference_lists(store, pack),
        )
        by_rule = {f.rule_id: f for f in outcome.findings}
        rules = {r.id: r for r in pack.rules}
        keys = store.select("answer_key", eq={"evaluation_case_id": case["id"]})
        case_results = []
        for k in keys:
            f = by_rule.get(k["rule_id"])
            rule = rules[k["rule_id"]]
            quote_valid: bool | None = None
            if f and f.evidence_quote is not None:
                quote_valid = f.quote_verified
            elif f and f.supporting_quotes:
                quote_valid = all(q.get("verified") for q in f.supporting_quotes)
            predicted = f.ai_status if f else None
            case_results.append(
                CaseResult(
                    case_code=case["case_code"], family_id=case.get("family_id"), style=case.get("language_style_tag"),
                    rule_code=rule.rule_code, rule_type=rule.rule_type, rule_id=rule.id, expected=k["expected_status"],
                    predicted=predicted, correct=predicted == k["expected_status"], quote_valid=quote_valid,
                    language_flag=bool(f and f.language_flag), error=bool(f and f.error_flag), case_id=case["id"],
                    valid=bool(f and f.is_valid),
                )
            )
        report.results.extend(sorted(case_results, key=lambda r: r.rule_code))

        if "injection" in (case.get("notes") or "").lower():
            unexpected = [r.rule_code for r in case_results if r.predicted == "Met" and r.expected != "Met"]
            report.injection.append(
                {
                    "case_code": case["case_code"],
                    "flagged": bool(outcome.injection_flags),
                    "patterns": sorted({fl.pattern for fl in outcome.injection_flags}),
                    "obeyed": bool(unexpected),
                    "unexpected_met": unexpected,
                }
            )

    # Twin consistency per (family, rule).
    groups: dict[tuple[str, str], list[CaseResult]] = defaultdict(list)
    for r in report.results:
        if r.family_id:
            groups[(r.family_id, r.rule_code)].append(r)
    for members in groups.values():
        consistent = len({m.predicted for m in members}) == 1 and len(members) > 1
        for m in members:
            m.twin_consistent = consistent

    report.finished_at = datetime.now(timezone.utc).isoformat()
    if llm:
        report.llm_stats = dict(llm.stats)

    if write_db:
        run = store.insert(
            "evaluation_runs",
            {
                "model_name": report.model_name,
                "prompt_version": PROMPT_VERSION,
                "started_at": report.started_at,
                "finished_at": report.finished_at,
                "summary": report.summary() | {"provider": provider_label},
                "report_markdown": report.markdown(),
            },
        )[0]
        rows = [
            {
                "eval_run_id": run["id"],
                "evaluation_case_id": r.case_id,
                "rule_id": r.rule_id,
                "predicted_status": r.predicted,
                "expected_status": r.expected,
                "correct": r.correct,
                "quote_valid": r.quote_valid,
                "twin_consistent": r.twin_consistent,
            }
            for r in report.results
        ]
        if rows:
            store.insert("evaluation_results", rows)
    return report
