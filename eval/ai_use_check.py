"""Fairness gate for the optional AI-use check (sub-step 3.3).

    python -m eval.ai_use_check --generate   # make sets (d) and (e) with the project's Gemini client (needs GEMINI_API_KEY)
    python -m eval.ai_use_check              # score every set with the LOCAL detector, set thresholds, apply the gate,
                                             # write eval/reports/ai_use_check.md (+ .json) and config/ai_use_check.yaml

Sets (seed/ai_use/*.jsonl), all answering the four Study NT free-text questions:
  a  human, polished English           b  human, plain English           c  human, second-language English
  d  written by a current LLM          e  human text lightly edited by an LLM for grammar only

Calibration and gate (from the task brief):
- "Stronger signal" threshold: the lowest value at which the flag rate of EVERY human group (a, b, c) is at most 2%.
- "Weak signal" threshold (our assumption; the brief does not set it): the lowest value at which every human group's
  flag rate is at most 10%.
- A text is "flagged" when its result is Weak signal or Stronger signal.
- The check may be switched on only if the second-language flag rate is no more than 2 percentage points above the
  polished-English rate. We add one condition (the safest reading): the human sets must really be human-written. A gate
  measured on synthetic "human" text does not show how real applicants are treated, so it cannot switch the check on.
Results for (d) and (e) are reported as they are. Nothing here is tuned to make them look better.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.pipeline import ai_use as detector

ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "seed" / "ai_use"
REPORT = ROOT / "eval" / "reports" / "ai_use_check.md"
REPORT_JSON = ROOT / "eval" / "reports" / "ai_use_check.json"
CONFIG = detector.CONFIG_PATH
GROUPS = {"a": "Human, polished English", "b": "Human, plain English", "c": "Human, second-language English",
          "d": "Written by a current LLM", "e": "Human text, grammar edited by an LLM"}
FILES = {"a": "a_human_polished.jsonl", "b": "b_human_plain.jsonl", "c": "c_human_second_language.jsonl",
         "d": "d_llm_written.jsonl", "e": "e_llm_grammar_edited.jsonl"}
HUMAN = ("a", "b", "c")
STRONGER_MAX_RATE = 0.02
WEAK_MAX_RATE = 0.10
GATE_GAP = 0.02


def load_sets() -> dict[str, list[dict[str, Any]]]:
    out = {}
    for g, name in FILES.items():
        p = SETS / name
        out[g] = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []
    return out


# ---------------------------------------------------------------- calibration and gate (pure, tested)

def lowest_threshold(groups: dict[str, list[float]], max_rate: float) -> float:
    """The lowest threshold t such that, in every group, the share of scores >= t is at most `max_rate`."""
    t = 0.0
    for scores in groups.values():
        if not scores:
            continue
        allowed = math.floor(max_rate * len(scores) + 1e-9)       # how many texts may be flagged in this group
        ranked = sorted(scores, reverse=True)
        if allowed < len(ranked):
            t = max(t, ranked[allowed] + 1e-6)                     # just above the first text that must not be flagged
    return min(t, 1.0)


def flag_rate(scores: list[float], weak: float) -> float:
    return sum(s >= weak for s in scores) / len(scores) if scores else 0.0


def gate(rates: dict[str, float], human_provenance: bool) -> dict[str, Any]:
    gap = rates.get("c", 0.0) - rates.get("a", 0.0)
    rates_ok = gap <= GATE_GAP + 1e-12
    return {"second_language_rate": rates.get("c", 0.0), "polished_rate": rates.get("a", 0.0), "gap": gap, "limit": GATE_GAP,
            "rates_ok": rates_ok, "human_provenance": human_provenance, "enabled": bool(rates_ok and human_provenance)}


# ---------------------------------------------------------------- scoring

def score_sets(sets: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    score, count, _info = detector.local_detector()
    cfg = detector.load_config()
    out: dict[str, list[dict[str, Any]]] = {}
    for g, rows in sets.items():
        res = detector.check_fields({r["id"]: r["text"] for r in rows}, scorer=score, count_tokens=count,
                                    thresholds={"weak": 2.0, "stronger": 2.0}, min_words=80, window_tokens=cfg.get("window_tokens", 400))
        out[g] = [{"id": r.field, "words": r.words, "mean": r.mean} for r in res]
    return out


def run() -> dict[str, Any]:
    sets = load_sets()
    scored = score_sets(sets)
    human_scores = {g: [x["mean"] for x in scored[g] if x["mean"] is not None] for g in HUMAN}
    stronger = lowest_threshold(human_scores, STRONGER_MAX_RATE)
    weak = min(lowest_threshold(human_scores, WEAK_MAX_RATE), stronger)
    rates, strong_rates, counts = {}, {}, {}
    for g in GROUPS:
        s = [x["mean"] for x in scored[g] if x["mean"] is not None]
        rates[g] = flag_rate(s, weak)
        strong_rates[g] = flag_rate(s, stronger)
        counts[g] = {"texts": len(scored[g]), "checked": len(s), "too_short": len(scored[g]) - len(s)}
    provenance = {g: sorted({r.get("provenance", "unknown") for r in sets[g]}) for g in GROUPS}
    human_ok = all(sets[g] and all(str(r.get("provenance", "")).startswith("human") for r in sets[g]) for g in HUMAN)
    result = {"date": date.today().isoformat(), "model": detector.load_config()["model"], "revision": detector.load_config()["revision"],
              "thresholds": {"weak": round(weak, 6), "stronger": round(stronger, 6)}, "flag_rate": rates, "stronger_rate": strong_rates,
              "counts": counts, "provenance": provenance, "gate": gate(rates, human_ok),
              "scores": {g: [round(x["mean"], 4) if x["mean"] is not None else None for x in scored[g]] for g in GROUPS}}
    return result


def write_outputs(result: dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    cfg = detector.load_config()
    cfg.update({"thresholds": result["thresholds"], "enabled": result["gate"]["enabled"],
                "gate": {k: result["gate"][k] for k in ("second_language_rate", "polished_rate", "gap", "limit", "rates_ok", "human_provenance")}
                | {"date": result["date"]}})
    header = "\n".join(l for l in CONFIG.read_text(encoding="utf-8").splitlines() if l.startswith("#")) + "\n" if CONFIG.exists() else ""
    CONFIG.write_text(header + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    REPORT.write_text(report(result), encoding="utf-8")


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def report(r: dict[str, Any]) -> str:
    g = r["gate"]
    L = ["# AI-use check: fairness evaluation", "",
         f"Run {r['date']} with `{r['model']}` (revision `{r['revision'][:12]}`), locally on CPU. Every number below is printed as measured.", "",
         "## Result", "",
         f"- **Check enabled: {'YES' if g['enabled'] else 'NO'}.**",
         f"- Second-language flag rate {pct(g['second_language_rate'])} vs polished English {pct(g['polished_rate'])}: a gap of "
         f"{100 * g['gap']:.1f} points (limit {100 * g['limit']:.0f}). Rate test: {'passed' if g['rates_ok'] else 'FAILED'}.",
         f"- Human sets really human-written: {'yes' if g['human_provenance'] else '**no**'}."]
    if not g["human_provenance"]:
        L += ["", "> The \"human\" sets (a), (b) and (c) are synthetic: an AI assistant wrote them to imitate each writing style. They are",
              "> not human writing, so this run cannot show how real applicants are treated. The check stays off until the sets are",
              "> replaced with consented, de-identified human answers and the evaluation is run again."]
    L += ["", f"Thresholds set by this run: Weak signal from **{r['thresholds']['weak']:.4f}**, Stronger signal from **{r['thresholds']['stronger']:.4f}** "
          "(mean detector output across a text's windows).",
          "Stronger is the lowest value that flags at most 2% of every human group; Weak (our assumption) at most 10%.", "",
          "## Per group", "", "| Group | Texts | Flagged (Weak or Stronger) | Stronger signal | Provenance |", "|---|---|---|---|---|"]
    for key, name in GROUPS.items():
        c = r["counts"][key]
        L.append(f"| ({key}) {name} | {c['checked']} | {pct(r['flag_rate'][key])} | {pct(r['stronger_rate'][key])} | {'; '.join(r['provenance'][key]) or 'missing'} |")
    L += ["", f"Detection rate on (d), text written by a current LLM: **{pct(r['flag_rate']['d'])}** flagged, {pct(r['stronger_rate']['d'])} as Stronger signal.",
          f"Human text edited by an LLM for grammar only (e): **{pct(r['flag_rate']['e'])}** flagged. Under the form's rule, grammar help is allowed, so any flag here is a false alarm.", "",
          "## Limits", "",
          "- Small samples (" + ", ".join(f"{k}: {r['counts'][k]['checked']}" for k in GROUPS) + " texts). With 12 texts, one text is 8.3 points, so a 2% rate can only be met by flagging none.",
          "- The thresholds are set on the same texts they are then measured on (in-sample). Real rates on new text will be higher.",
          "- The detector was trained on GPT-2 output (2019). It is known to miss text from current models and to mark plain and",
          "  second-language writing as AI-like. It cannot tell allowed help (spelling, grammar, organising ideas) from disallowed use.",
          "", "## Raw scores", "", "Mean detector output per text, for audit. Never shown to officers or applicants.", ""]
    for key in GROUPS:
        L.append(f"- ({key}): " + ", ".join("-" if s is None else f"{s:.3f}" for s in r["scores"][key]))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- making sets (d) and (e) with the project's Gemini client

def generate() -> None:
    from pydantic import BaseModel

    from app.config import get_settings
    from app.llm.base import LLMError, Prompt
    from app.llm.client import build_llm_client

    class TextOut(BaseModel):
        text: str

    llm = build_llm_client(get_settings())
    if not llm.model_name.startswith("gemini"):
        raise SystemExit("Set LLM_PROVIDER=gemini and GEMINI_API_KEY to make sets (d) and (e).")
    sets = load_sets()
    personas = ["a student applying to study nursing", "a student applying to study engineering", "a student applying to study teaching"]
    questions = sorted({(r["prompt"], r["question"]) for r in sets["a"]})

    def ask(task: str, system: str, user: str) -> str | None:
        p = Prompt(task=task, system=system, user=user, prompt_version="ai-use-eval-1", cache_text=user, rule_pack_version="eval")
        try:
            return llm.call_llm(p, TextOut).text.strip()
        except LLMError as exc:
            print("skipped", task, type(exc).__name__)
            return None

    # Keep what an earlier (rate-limited) run already made; only fill the gaps.
    d_rows = list(sets["d"])
    have_d = {(r["prompt"], r.get("persona")) for r in d_rows}
    for i, (key, q) in enumerate(questions):
        for j, who in enumerate(personas):
            if (key, who) in have_d:
                continue
            t = ask(f"d:{key}:{j}", "You write grant application answers.",
                    f"Write a 100 to 130 word answer, in the first person, as {who}, to this scholarship application question: {q}")
            if t:
                d_rows.append({"id": f"d{len(d_rows) + 1:02d}", "group": "d", "prompt": key, "question": q, "persona": who, "text": t,
                               "provenance": f"llm:{llm.model_name}"})
    e_rows = list(sets["e"])
    have_e = {r.get("source") for r in e_rows}
    for src in sets["b"][:6] + sets["c"][:6]:
        if src["id"] in have_e:
            continue
        t = ask(f"e:{src['id']}", "You are a careful proofreader.",
                "Correct ONLY the spelling and grammar of this text. Do not change the wording, the order, the structure or the meaning, "
                "and do not add or remove ideas. Return the corrected text only.\n\n" + src["text"])
        if t:
            e_rows.append({"id": f"e{len(e_rows) + 1:02d}", "group": "e", "prompt": src["prompt"], "question": src["question"], "text": t,
                           "source": src["id"], "provenance": f"{src['provenance']} Grammar edited by llm:{llm.model_name}."})
    for name, rows in (("d", d_rows), ("e", e_rows)):
        if rows:
            (SETS / FILES[name]).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(name, len(rows), "texts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generate", action="store_true", help="make sets (d) and (e) with the Gemini client first")
    args = ap.parse_args()
    if args.generate:
        generate()
    result = run()
    write_outputs(result)
    print(report(result))
