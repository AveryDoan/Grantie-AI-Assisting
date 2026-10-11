"""Evaluation of the merit summaries on the Sita Karki sample set.

    python -m eval.merit_summary_eval            # runs the full flow in-process and writes docs/merit_summary_eval.md

Metrics (all by code, from what the officer would see):
  key-fact recall            how many of the listed facts appear as a verified claim, with the right documents
  unsupported-claim rate     bullets whose numbers are not all in the quotes they cite (must be 0 for anything shown)
  quote validity             verified quotes that really are in the document's original text
  same claims across wording the same facts written polished, plain and second-language give the same facts

The key facts were written from the sample documents BEFORE looking at any output, and the prompt and the keyword lists are
not tuned to them. Failures are reported as they are. With LLM_PROVIDER=stub the "AI" is a keyword matcher (labelled in the
report); its numbers say nothing about a real model.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# (criterion, fact, [needles that must all be inside ONE verified quote], [document label fragments that must each be cited])
KEY_FACTS: list[tuple[str, str, list[str], list[str]]] = [
    ("M1", "GPA 3.84", ["3.84"], ["Application form"]),
    ("M1", "Rank 3rd of 142", ["3rd", "142"], ["Application form"]),
    ("M1", "Dean's Merit Award 2023, 2024, 2025 (application)", ["Dean's Merit Award", "2023"], ["Application form"]),
    ("M1", "Dean's Merit Award 2025 (certificate)", ["2025"], ["Certificate"]),
    ("M1", "First place, National Student Innovation Showcase 2025", ["first place", "Showcase"], ["Application form"]),
    ("M1", "Himalayan Computing Conference paper", ["Himalayan Computing Conference"], ["Application form"]),
    ("M3", "Founded Code Pahadi", ["Code Pahadi"], ["Application form"]),
    ("M3", "Code Pahadi: 6 schools, 180+ participants", ["180", "6 schools"], ["Application form"]),
    ("M3", "Code Pahadi: 14 mentors", ["14", "mentors"], ["Application form"]),
    ("M3", "Women in Technology Society presidency", ["Women in Technology Society"], ["Application form"]),
    ("M3", "Membership 40 to 125", ["40 to 125"], ["Application form"]),
    ("M3", "12 internships", ["12", "internships"], ["Application form"]),
    ("M3", "Flood recovery: 6 volunteers, 300 villagers", ["6 volunteers", "300 villagers"], ["Application form"]),
    ("M3", "Flood recovery: 41 small businesses", ["41", "businesses"], ["Application form"]),
    ("M5", "Plans to volunteer", ["volunteer"], ["Application form"]),
    ("M5", "Plans to mentor", ["mentor"], ["Application form"]),
    ("M5", "Territory hackathons", ["hackathons"], ["Application form"]),
    ("M5", "A Code Pahadi chapter in Darwin", ["chapter in Darwin"], ["Application form"]),
    ("M5", "Plan to work in the Territory", ["work in the Territory"], ["Application form"]),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).casefold()


def _numbers(s: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def evaluate(detail: dict[str, Any]) -> dict[str, Any]:
    sums = detail.get("merit_summaries") or {}
    texts = detail.get("source_texts") or {}
    facts = []
    for code, name, needles, docs in KEY_FACTS:
        s = sums.get(code)
        quotes = [(c, it) for t in (s["themes"] if s else []) for it in t["items"] for c in it["quotes"]]
        hit = next(((c, it) for c, it in quotes if all(_norm(n) in _norm(c["text"] or "") for n in needles)), None)
        sources_ok = bool(hit) and all(any(_norm(d) in _norm(c["name"]) for c, _ in quotes if all(_norm(n) in _norm(c["text"] or "") for n in needles))
                                       for d in docs[:1]) if docs else bool(hit)
        facts.append({"criterion": code, "fact": name, "found": bool(hit), "right_document": sources_ok,
                      "differs": bool(hit and hit[1]["differs"])})
    items = [it for s in sums.values() for t in s["themes"] for it in t["items"]]
    unsupported = 0
    for s in sums.values():
        # document names such as "Referee letter 1" are not facts: their digits are not checked
        names = sorted({d["name"] for d in s["coverage"]["documents"]}, key=len, reverse=True)
        for t in s["themes"]:
            for it in t["items"]:
                allowed = set().union(*(_numbers(c["text"] or "") for c in it["quotes"])) if it["quotes"] else set()
                text = it["text"] or ""
                for n in names:
                    text = re.sub(re.escape(n), " ", text, flags=re.I)
                if not it["quotes"] or not _numbers(text) <= allowed:
                    unsupported += 1
    claims = [c for s in sums.values() for t in s["themes"] for it in t["items"] for c in it["quotes"]]
    valid = sum(1 for c in claims if c["span"] and _norm(texts.get(c["span"]["source"], {}).get("text", "")[c["span"]["start"]:c["span"]["end"]]) == _norm(c["text"] or ""))
    return {
        "facts": facts,
        "recall": sum(f["found"] and f["right_document"] for f in facts) / len(facts),
        "recall_by_criterion": {c: (sum(f["found"] and f["right_document"] for f in facts if f["criterion"] == c), sum(f["criterion"] == c for f in facts))
                                for c in sorted({f["criterion"] for f in facts})},
        "bullets": len(items), "unsupported_rate": (unsupported / len(items)) if items else 0.0, "unsupported": unsupported,
        "quotes": len(claims), "quote_validity": (valid / len(claims)) if claims else 1.0,
        "criteria": {c: {"status": s["status"], "bullets": sum(len(t["items"]) for t in s["themes"]), "themes": len(s["themes"]),
                         "other_passages": len(s["other_passages"]), "could_not_verify": len(s["could_not_verify"]),
                         "documents_read": f"{s['coverage']['with_matches']} of {s['coverage']['document_count']}",
                         "documents_differ": sum(it["differs"] for t in s["themes"] for it in t["items"])} for c, s in sums.items()},
    }


def language_variants() -> dict[str, Any]:
    """Same facts written three ways: do they give the same set of facts?"""
    from tests.test_merit_claims import PLAIN, POLISHED, SECOND, _fact_set

    base = {n for _c, n in _fact_set(POLISHED)}
    out = {}
    for name, text in (("plain", PLAIN), ("second-language", SECOND)):
        other = {n for _c, n in _fact_set(text)}
        out[name] = {"same_numbers": set().union(*base) == set().union(*other), "polished_facts": len(base), "variant_facts": len(other)}
    return out


def report(m: dict[str, Any], variants: dict[str, Any], provider: str) -> str:
    L = ["# Merit summary evaluation (Sita Karki sample set)", "",
         f"AI provider for this run: **{provider}**." + (" This is the offline keyword stub, NOT a language model: these numbers describe the pipeline "
                                                         "(search, verification, grouping, coverage), not how a real model would read the documents." if provider == "stub" else ""), "",
         "The key facts were written from the sample documents before any output was looked at. Nothing was tuned to them. Failures are shown as they are.", "",
         "## Headline", "",
         f"- Key-fact recall: **{m['recall']:.0%}** ({sum(f['found'] and f['right_document'] for f in m['facts'])} of {len(m['facts'])})  by criterion: "
         + ", ".join(f"{c} {a}/{b}" for c, (a, b) in m["recall_by_criterion"].items()),
         f"- Unsupported-claim rate: **{m['unsupported_rate']:.1%}** ({m['unsupported']} of {m['bullets']} bullets). Must be 0 for anything shown.",
         f"- Quote validity: **{m['quote_validity']:.1%}** of {m['quotes']} verified quotes sit at their exact place in the original document text.",
         "- Same facts across wording: " + "; ".join(f"{k}: {'same' if v['same_numbers'] else 'DIFFERENT'} ({v['variant_facts']} vs {v['polished_facts']})" for k, v in variants.items()), "",
         "## Per criterion", "", "| Criterion | Status | Bullets | Themes | Other passages found | Could not verify | Documents read | Documents differ |", "|---|---|---|---|---|---|---|---|"]
    for c, v in m["criteria"].items():
        L.append(f"| {c} | {v['status']} | {v['bullets']} | {v['themes']} | {v['other_passages']} | {v['could_not_verify']} | {v['documents_read']} | {v['documents_differ']} |")
    L += ["", "## Key facts", "", "| Criterion | Fact | Found as a verified claim | Right document | Marked Documents differ |", "|---|---|---|---|---|"]
    for f in m["facts"]:
        L.append(f"| {f['criterion']} | {f['fact']} | {'yes' if f['found'] else '**NO**'} | {'yes' if f['right_document'] else '**NO**'} | {'yes' if f['differs'] else 'no'} |")
    return "\n".join(L) + "\n"


def main() -> None:
    sys.path.insert(0, str(ROOT))
    import os

    from tests.e2e import test_demo_sita as t

    gen = t.run.__wrapped__()
    r = next(gen)
    aid = r["id"]
    d = t._get(r, f"/applications/{aid}/documents-step")
    for s in d["slots"]:
        if s["file_name"] and s["required"]:
            t._post(r, f"/applications/{aid}/documents-step/decision", json={"slot": s["slot"], "decision": "confirmed"})
    t._post(r, f"/applications/{aid}/steps/documents/complete")
    t._post(r, f"/applications/{aid}/redaction-check/approve")
    t._post(r, f"/applications/{aid}/assess", json={})
    detail = t._get(r, f"/applications/{aid}")
    m = evaluate(detail)
    text = report(m, language_variants(), os.environ.get("LLM_PROVIDER", "stub"))
    out = ROOT / "docs" / "merit_summary_eval.md"
    out.write_text(text, encoding="utf-8")
    print(text, flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
