"""Evaluation of the consistency layer on the synthetic cases F01 to F10 and one clean control from the N cases.

    python -m eval.consistency --llm stub                 # offline keyword stub (NOT an LLM)
    python -m eval.consistency --llm gemini               # Gemini, through GuardedLLM: REDACTED text only

Writes eval/consistency_eval.md. Each run replaces its own section, so a stub run and a Gemini run sit side by side.

What it measures, per check type: of the flags a case SHOULD raise, which did it raise (recall), and of the flags
raised, which were not expected (precision). Controls are reported separately: they must raise no strong flag.

The limits are stated in the report: the cases were written alongside the checks, there are only 11 of them, and
nothing here says how the checks behave on real applications.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.pipeline.consistency.models import CHECK_TYPES

REPORT = Path(__file__).resolve().parent / "consistency_eval.md"
CONTROL_N_CASE = "N08"   # a clean existing case (plain style) to show precision


@dataclass
class CaseResult:
    code: str
    control: bool
    should: list[str]
    may: list[str]
    should_not: list[str]
    raised: list[tuple[str, str]]            # (check_id, strength) of verified flags
    unclear: int = 0
    dropped_unverified: int = 0
    notes: str = ""

    @property
    def raised_ids(self) -> set[str]:
        return {c for c, _ in self.raised}

    @property
    def hits(self) -> list[str]:
        return [c for c in self.should if c in self.raised_ids]

    @property
    def missed(self) -> list[str]:
        return [c for c in self.should if c not in self.raised_ids]

    @property
    def unexpected(self) -> list[tuple[str, str]]:
        ok = set(self.should) | set(self.may)
        return sorted({(c, s) for c, s in self.raised if c not in ok})

    @property
    def violations(self) -> list[str]:
        """Raised flags that the case lists as ones it should NOT raise."""
        return sorted({c for c, _ in self.raised if any(fnmatch.fnmatch(c, pat) for pat in self.should_not)})

    @property
    def strong(self) -> int:
        return sum(1 for _, s in self.raised if s == "strong")


def run_cases(llm: Any, *, echo=print) -> tuple[list[CaseResult], dict[str, Any]]:
    """Seed the cases, assess each with `llm`, and collect the flags raised."""
    from app.config import Settings
    from app.pipeline.orchestrator import run_assessment
    from app.services.access import Actor
    from app.store.memory import MemoryStore
    from redaction.crypto import generate_key
    from seed import data
    from seed.fraud.load import expected, seed_fraud
    from seed.run import seed

    store = MemoryStore()
    seed(store)
    ids = seed_fraud(store)
    ids[CONTROL_N_CASE] = data.sid(f"application:{CONTROL_N_CASE}")
    uid = data.sid("user:consistency-eval")
    store.insert("profiles", {"id": uid, "role": "officer", "organisation_id": data.ORG_ID})
    actor = Actor(user_id=uid, role="officer", organisation_id=data.ORG_ID)
    settings = Settings(_env_file=None, llm_provider="stub", redaction_key=generate_key(), consistency_layer=True,
                        identifier_hash_key="evaluation-only-hash-key-0123456789abcdef")
    exp = expected()
    stats = {"cases": 0, "failed": []}
    runs: dict[str, dict[str, Any]] = {}
    for code in [*exp.keys(), CONTROL_N_CASE]:
        try:
            runs[code] = run_assessment(store, actor, ids[code], llm, settings, force=True)
            echo(f"{code}: assessed")
        except Exception as exc:   # an assessment that cannot run is reported, never hidden
            stats["failed"].append(f"{code}: {type(exc).__name__}")
            echo(f"{code}: assessment failed ({type(exc).__name__})")
    # Read the flags only after the whole pool is assessed: links between applications appear as members join it.
    results: list[CaseResult] = []
    for code, run in runs.items():
        e = exp.get(code, {"control": True, "should_raise": [], "may_raise": [], "should_not_raise": ["*"]})
        rows = store.select("consistency_flags", eq={"application_id": ids[code]})
        trace = (store.select("assessment_runs", eq={"id": run["id"]}, limit=1) or [{}])[0].get("consistency_trace") or {}
        results.append(CaseResult(
            code=code, control=bool(e["control"]), should=list(e["should_raise"]), may=list(e["may_raise"]),
            should_not=list(e["should_not_raise"]),
            raised=sorted({(r["check_id"], r["strength"]) for r in rows if r["verification"] == "verified"}),
            unclear=sum(r["verification"] == "unclear" for r in rows),
            dropped_unverified=sum((trace.get(k) or {}).get("dropped_unverified", 0) for k in ("timeline", "narrative"))))
        stats["cases"] += 1
    return results, stats


def by_type(results: list[CaseResult]) -> dict[str, dict[str, int]]:
    table = {t: {"expected": 0, "hit": 0, "missed": 0, "raised": 0, "unexpected": 0} for t in CHECK_TYPES}
    for r in results:
        for c in r.should:
            t = c.split(".")[0]
            table[t]["expected"] += 1
            table[t]["hit" if c in r.raised_ids else "missed"] += 1
        for c in r.raised_ids:
            table[c.split(".")[0]]["raised"] += 1
        for c, _ in r.unexpected:
            table[c.split(".")[0]]["unexpected"] += 1
    return table


def pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{n}/{d} ({n / d:.0%})"


def section(tag: str, label: str, results: list[CaseResult], stats: dict[str, Any], notes: list[str]) -> str:
    cases = [r for r in results if not r.control]
    controls = [r for r in results if r.control]
    table = by_type(results)
    out = [f"<!-- run:{tag} -->", f"## Run: {label}", "",
           f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. {stats['cases']} cases assessed"
           + (f"; **failed to run: {', '.join(stats['failed'])}**" if stats["failed"] else "") + ".", ""]
    out += notes + [""]
    out += ["### Per check type", "", "| Check type | Expected flags | Raised (recall) | Missed | Flags raised | Unexpected | Precision |", "|---|---|---|---|---|---|---|"]
    for t in CHECK_TYPES:
        row = table[t]
        tp = row["hit"]
        out.append(f"| {t} | {row['expected']} | {pct(tp, row['expected'])} | {row['missed']} | {row['raised']} | {row['unexpected']} | "
                   f"{pct(row['raised'] - row['unexpected'], row['raised'])} |")
    out += ["", "Precision here is the share of flags raised that the case expected. Flags a case lists as allowed (weak signals or "
            "a near-duplicate of an expected one) count as expected.", "",
            "### Per case (planted problems)", "", "| Case | Should raise | Raised it | Missed | Unexpected flags | Strong | Unclear |", "|---|---|---|---|---|---|---|"]
    for r in cases:
        out.append(f"| {r.code} | {', '.join(f'`{c}`' for c in r.should) or '-'} | {len(r.hits)}/{len(r.should)} | "
                   f"{', '.join(f'`{c}`' for c in r.missed) or '-'} | {', '.join(f'`{c}` ({s})' for c, s in r.unexpected) or '-'} | {r.strong} | {r.unclear} |")
    out += ["", "### Controls (precision)", "",
            "Innocent cases. They must raise **no strong flag**. Weak signals are listed because they are the false-positive risk "
            "(scans, re-saved PDFs, a short agreed late arrival).", "", "| Case | Strong flags | Weak flags raised | Unexpected | Result |", "|---|---|---|---|---|"]
    for r in controls:
        weak = [c for c, s in r.raised if s == "weak"]
        out.append(f"| {r.code} | {r.strong} | {', '.join(f'`{c}`' for c in weak) or '-'} | "
                   f"{', '.join(f'`{c}` ({s})' for c, s in r.unexpected) or '-'} | {'pass' if r.strong == 0 else '**FAIL: strong flag on a control**'} |")
    viol = [(r.code, v) for r in results for v in r.violations if not r.control]
    out += ["", f"Flags a case lists as ones it should NOT raise, but did: {', '.join(f'{c}: `{v}`' for c, v in viol) if viol else 'none'}.", ""]
    dropped = sum(r.dropped_unverified for r in results)
    out += [f"AI items dropped because their quote could not be verified in the text: **{dropped}**. "
            f"Items kept only as 'unclear' (no quotes shown): **{sum(r.unclear for r in results)}**.", "<!-- /run:" + tag + " -->", ""]
    return "\n".join(out)


HEADER = """# Consistency layer evaluation

Signals, never verdicts: these flags point at things an officer should check. They are not scores, ratings or recommendations.

**Read this first.**
- The 11 cases (F01 to F10 and one clean N case) were **written alongside the checks**. They show that each check can find a
  problem it was built to find, not how it performs on real applications.
- The cases are small and synthetic (fictional names, example.com addresses, ACMA-reserved phone numbers).
- The offline stub is a pattern matcher, **not an LLM**, and was written for these cases. The Gemini run is the only
  AI-assisted result that says anything about a real model, and even it is 11 cases.
- A real pool, real scans, real edited PDFs and real template letters will produce more false positives than this.

"""


def write(tag: str, text: str, path: Path = REPORT) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    body = existing.split("\n", 1)[1] if existing.startswith("# ") and "<!-- run:" not in existing.split("\n", 1)[0] else existing
    sections = dict(re.findall(r"<!-- run:(\w+) -->(.*?)<!-- /run:\1 -->", existing, re.S))
    sections[tag] = "\n" + text.split(f"<!-- run:{tag} -->", 1)[1].rsplit(f"<!-- /run:{tag} -->", 1)[0]
    order = [t for t in ("stub", "gemini") if t in sections] + [t for t in sections if t not in ("stub", "gemini")]
    path.write_text(HEADER + "\n".join(f"<!-- run:{t} -->{sections[t]}<!-- /run:{t} -->\n" for t in order), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", choices=["stub", "gemini"], default="stub")
    parser.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args()
    from app.llm import LLMClient, build_llm_client
    from app.llm.cache import MemoryCache
    from app.llm.stub import OfflineStubProvider

    if args.llm == "gemini":
        from app.config import get_settings

        settings = get_settings()
        llm = build_llm_client(settings.model_copy(update={"llm_provider": "gemini", "llm_cache_enabled": False}), cache=MemoryCache())
        label = f"Gemini ({llm.model_name}), REDACTED text only"
        notes = ["The AI-assisted checks (timeline, narrative, and the referee-letter fields) ran on a real model, through `GuardedLLM`, which only "
                 "allows leak-scanned redacted text. Model output varies from run to run."]
        tag = "gemini"
    else:
        llm = LLMClient(OfflineStubProvider(), temperature=0.0)
        label = "offline keyword stub (NOT an LLM)"
        notes = ["The AI-assisted checks ran on the offline stub: pattern matching written alongside these cases. These numbers show the "
                 "plumbing works (quotes, verification, persistence); they say nothing about a real model."]
        tag = "stub"
    results, stats = run_cases(llm)
    write(tag, section(tag, label, results, stats, notes), args.out)
    table = by_type(results)
    print("Per type (recall of expected flags): " + ", ".join(f"{t} {pct(v['hit'], v['expected'])}" for t, v in table.items()))
    print(f"Controls with a strong flag: {[r.code for r in results if r.control and r.strong]}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
