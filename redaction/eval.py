"""Redaction evaluation report (synthetic data only).

    python -m redaction.eval                     # writes eval/reports/redaction_eval.md
    python -m redaction.eval --out report.md --no-twins

Measures, on the labelled synthetic set in redaction.evalset:
  1. Recall: is every labelled personal value gone from what the LLM would
     receive? A name counts as leaked if ANY part of it (3+ letters) survives.
     Leaks are also split by whether the leak scan blocked the LLM call.
  2. Over-redaction: terms the rules need (providers, courses, dates, NT
     places, countries, roles, durations) that were wrongly removed. Every
     other redaction that matches no labelled value is listed for review.
  3. Name recall by culture: detector only (no known values) and with
     known values, across several sentence templates.
  4. Findings stability: the twin set (N01/N08/N09, same facts in polished,
     plain and second-language English) assessed with and without redaction
     by the offline keyword stub. The stub is NOT an LLM; the unredacted
     baseline never leaves this process and is refused for any real provider.

Everything is synthetic; the report quotes values freely for that reason.
Never point this at real applicant data.
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from redaction.config import default_config
from redaction.detector import DETECTOR_VERSION, Detector
from redaction.evalset import NAMES, TEMPLATES, EvalCase, all_cases
from redaction.pipeline import run_redaction
from redaction.recognizers import KnownValue, _date_variants
from redaction.structured import original_application_text

REPORT = Path(__file__).resolve().parent.parent / "eval" / "reports" / "redaction_eval.md"
NAME_TYPES = {"PERSON", "REFEREE"}
PARTICLES = {"van", "thi", "dela", "de", "la", "al", "bin", "binti", "da", "dos", "von"}


@dataclass
class ValueResult:
    case_id: str
    group: str
    value: str
    entity: str
    present: bool
    leaked: str | None = None   # None, "full", or "part: <word>" / "variant: <text>"
    blocked: bool = False       # the leak scan stopped the LLM call for this case

    @property
    def exposed(self) -> bool:
        return bool(self.leaked) and not self.blocked


@dataclass
class KeepResult:
    case_id: str
    term: str
    removed: bool


@dataclass
class EvalResults:
    values: list[ValueResult] = field(default_factory=list)
    keeps: list[KeepResult] = field(default_factory=list)
    unlabelled: list[tuple[str, str, str, bool]] = field(default_factory=list)  # (case, type, original, expected)
    statuses: dict[str, str] = field(default_factory=dict)
    names: list[dict] = field(default_factory=list)
    twins: dict | None = None
    seconds: float = 0.0


# ---------------------------------------------------------------- leak checks


def _contains(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, re.IGNORECASE) is not None


def _digits_pattern(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return None
    tail = digits[-8:]  # national part: survives any prefix or formatting
    return r"[\s\-.()]*".join(tail)


def leak_of(value: str, entity: str, ai_text: str) -> str | None:
    if _contains(ai_text, value):
        return "full"
    if entity in NAME_TYPES:
        for part in re.split(r"[\s,]+", value):
            for piece in {part, *part.split("-")}:
                if len(piece) >= 3 and piece.casefold() not in PARTICLES and _contains(ai_text, piece):
                    return f"part: {piece}"
    if entity == "DOB":
        for v in _date_variants(value):
            if _contains(ai_text, v):
                return f"variant: {v}"
    if entity == "PHONE":
        pat = _digits_pattern(value)
        if pat and (m := re.search(pat, ai_text)):
            return f"variant: {m.group(0)}"
    return None


def _labelled(original: str, personal: list[tuple[str, str]]) -> bool:
    o = original.casefold()
    for value, _ in personal:
        v = value.casefold()
        if o in v or v in o or any(len(p) >= 3 and p in o.split() for p in v.replace(",", " ").split()):
            return True
    return False


# ---------------------------------------------------------------- 1 + 2: recall and over-redaction


def evaluate_cases(cases: list[EvalCase], res: EvalResults, detector: Detector) -> None:
    for case in cases:
        out = run_redaction(case.case_id, case.application_text, case.documents,
                            document_kinds=case.kinds or None, detector=detector)
        res.statuses[case.case_id] = out.ai_status
        ai_text = "\n".join(out.ai_texts().values())
        original = "\n".join([original_application_text(case.application_text), *(d.text for d in case.documents)])
        for value, entity in case.personal:
            present = _contains(original, value)
            leaked = leak_of(value, entity, ai_text) if present else None
            res.values.append(ValueResult(case.case_id, case.group, value, entity, present, leaked, not out.llm_allowed))
        for term in case.keep:
            if _contains(original, term):
                res.keeps.append(KeepResult(case.case_id, term, not _contains(ai_text, term)))
        seen = set()
        for occs in out.token_map.occurrences.values():
            for o in occs:
                ttype = out.token_map.entries[o.token].token_type
                key = (ttype, o.original.casefold())
                if key not in seen and not _labelled(o.original, case.personal):
                    seen.add(key)
                    expected = _labelled(o.original, [(x, "") for x in case.expected_other])
                    res.unlabelled.append((case.case_id, ttype, o.original, expected))


# ---------------------------------------------------------------- 3: name recall by culture


def _coverage(detector: Detector, text: str, name: str, known: list[KnownValue] | None) -> str:
    start = text.index(name)
    spans = [(d.start, d.end) for d in detector.detect(text, known)]
    chars = [i for i in range(start, start + len(name)) if not text[i].isspace()]
    covered = sum(any(s <= i < e for s, e in spans) for i in chars)
    return "full" if covered == len(chars) else ("partial" if covered else "missed")


def evaluate_names(res: EvalResults, detector: Detector) -> None:
    for group, names in NAMES.items():
        for name in names:
            for tpl in TEMPLATES:
                text = tpl.format(name=name)
                res.names.append({
                    "group": group, "name": name, "sentence": text,
                    "detector": _coverage(detector, text, name, None),
                    "known": _coverage(detector, text, name, [KnownValue(name, "PERSON", "applicant_name")]),
                })


# ---------------------------------------------------------------- 4: findings stability on the twin set


def _identity_outcome(app: dict, documents: list[dict]):
    """A pass-through 'redaction' for the in-process stub baseline only."""
    from app.services.redaction_service import extracted_documents
    from redaction.documents import RedactedDocument
    from redaction.leakscan import LeakScanResult
    from redaction.pipeline import RedactionOutcome
    from redaction.tokenizer import TokenMap

    cfg = default_config()
    docs = [RedactedDocument(d.document_id, d.status, False, d.include_in_ai_input, d.text if d.include_in_ai_input else "")
            for _, d, _ in extracted_documents(None, documents)]
    return RedactionOutcome(app["id"], "ok", "ready", original_application_text(app.get("application_text") or {}),
                            docs, TokenMap(cfg), [], "unknown", LeakScanResult(), {}, [])


def evaluate_twins(codes: tuple[str, ...] = ("N01", "N08", "N09")) -> dict:
    from unittest.mock import patch

    from app.config import Settings
    from app.llm import LLMClient
    from app.llm.stub import OfflineStubProvider
    from app.pipeline.orchestrator import assess_application, load_reference_lists
    from app.pipeline.rules_loader import load_rule_pack
    from app.store.base import one
    from app.store.memory import MemoryStore
    from seed import data
    from seed.run import seed

    store = MemoryStore()
    seed(store)
    settings = Settings(_env_file=None, consistency_check=False)
    provider = OfflineStubProvider()
    assert isinstance(provider, OfflineStubProvider)  # the unredacted baseline is for the local stub only

    def assess(app: dict, *, redacted: bool) -> dict[str, str]:
        docs = store.select("documents", eq={"application_id": app["id"]})
        pack = load_rule_pack(store, app["rule_pack_id"])
        kwargs = dict(application=app, documents=docs, pack=pack,
                      register_rows=store.select("mock_grants_register", eq={"applicant_id": app["applicant_id"]}),
                      llm=LLMClient(provider, temperature=0.0), settings=settings,
                      applicant=one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1)),
                      reference_lists=load_reference_lists(store, pack))
        if redacted:
            outcome = assess_application(**kwargs)
        else:
            with patch("redaction.pipeline.GuardedLLM", lambda inner, outcome, cfg=None: inner):
                outcome = assess_application(**kwargs, redaction=_identity_outcome(app, docs))
        return {f.rule_code: f.ai_status for f in outcome.findings}

    rows = {}
    for code in codes:
        app = store.select("applications", eq={"id": data.sid(f"application:{code}")})[0]
        expected = next(c for c in data.CASES if c["code"] == code)["expected"]
        rows[code] = {"before": assess(app, redacted=False), "after": assess(app, redacted=True), "expected": expected}
    return rows


# ---------------------------------------------------------------- report


def pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{n}/{d} ({n / d:.1%})"


def markdown(res: EvalResults) -> str:
    present = [v for v in res.values if v.present]
    leaks = [v for v in present if v.leaked]
    exposed = [v for v in leaks if v.exposed]
    removed = [k for k in res.keeps if k.removed]
    unexpected = [u for u in res.unlabelled if not u[3]]
    L: list[str] = []
    L += ["# Redaction evaluation report", "",
          f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} in {res.seconds:.0f} s. "
          f"Detector `{DETECTOR_VERSION}`, config `{default_config().config_hash[:12]}`.", "",
          "**Synthetic data only.** Every name, number and address below is fictional. "
          "This report shows how the redaction behaves on a small hand-made set; it is not a guarantee "
          "for real applications. Redaction cannot promise zero leaks.", ""]

    L += ["## Summary", "",
          "| Measure | Result |", "|---|---|",
          f"| Labelled personal values (present in the input) | {len(present)} |",
          f"| **Recall** (values fully removed from the redacted text) | {pct(len(present) - len(leaks), len(present))} |",
          f"| Values left in the redacted text (any part) | {len(leaks)} |",
          f"| ...of which would actually reach the LLM (leak scan did not block) | **{len(exposed)}** |",
          f"| **Over-redaction** (needed terms wrongly removed) | {pct(len(removed), len(res.keeps))} |",
          f"| **Unexpected redactions** (probable over-redaction, listed below) | {len(unexpected)} |",
          f"| Cases blocked by the leak scan (no LLM call) | "
          f"{sum(s != 'ready' for s in res.statuses.values())}/{len(res.statuses)} |", ""]
    if res.names:
        det = sum(n["detector"] == "full" for n in res.names)
        kn = sum(n["known"] == "full" for n in res.names)
        L += [f"Name recall (fully covered): detector only {pct(det, len(res.names))}; "
              f"with known values {pct(kn, len(res.names))}.", ""]
    if res.twins:
        changed = sum(1 for r in res.twins.values() for k in r["before"] if r["before"][k] != r["after"].get(k))
        total = sum(len(r["before"]) for r in res.twins.values())
        L += [f"Findings changed by redaction on the twin set: {changed}/{total} rule checks.", ""]

    L += ["## Results by entity type", "", "| Type | Present | Removed | Leaked | Reached LLM | Recall |", "|---|---|---|---|---|---|"]
    by_type: dict[str, list[ValueResult]] = defaultdict(list)
    for v in present:
        by_type[v.entity].append(v)
    for t, vs in sorted(by_type.items()):
        lk = [v for v in vs if v.leaked]
        L.append(f"| {t} | {len(vs)} | {len(vs) - len(lk)} | {len(lk)} | {sum(v.exposed for v in lk)} | "
                 f"{(len(vs) - len(lk)) / len(vs):.0%} |")
    L.append("")

    L += ["## Every miss (personal value left in the redacted text)", ""]
    if leaks:
        L += ["| Case | Group | Type | Value | What survived | LLM call |", "|---|---|---|---|---|---|"]
        for v in leaks:
            L.append(f"| {v.case_id} | {v.group} | {v.entity} | {v.value} | {v.leaked} | "
                     f"{'blocked by leak scan' if v.blocked else '**would be sent**'} |")
    else:
        L.append("None in this set.")
    not_present = [v for v in res.values if not v.present]
    if not_present:
        L += ["", f"Labelled values not found in their case's input (skipped, check the labels): "
              + ", ".join(f"{v.case_id} {v.entity} `{v.value}`" for v in not_present)]
    L.append("")

    L += ["## Over-redaction", "",
          "Terms the rules need. Removing one changes what the AI can judge (for example a provider name or a start date).", ""]
    if removed:
        L += ["| Case | Term wrongly removed |", "|---|---|"] + [f"| {k.case_id} | {k.term} |" for k in removed]
    else:
        L.append(f"None of the {len(res.keeps)} checked terms were removed.")
    L += ["", "### Unexpected redactions (probable over-redaction)", "",
          "Text replaced by a token that is neither a labelled personal value nor something the case says may rightly "
          "be redacted (an address city, a CoE code). Each one is a word the AI no longer sees.", ""]
    if unexpected:
        L += ["| Case | Token type | Original text |", "|---|---|---|"]
        L += [f"| {c} | {t} | {o.replace('|', '/').replace(chr(10), ' ')} |" for c, t, o, _ in unexpected]
    else:
        L.append("None.")
    expected = sorted({(t, o) for _, t, o, e in res.unlabelled if e})
    if expected:
        L += ["", "Expected extra redactions (not counted): " + ", ".join(f"{o} ({t})" for t, o in expected) + "."]
    L.append("")

    if res.names:
        L += ["## Name recall by culture", "",
              "Each name in each sentence template. *Detector only* = a person the system was not told about "
              "(e.g. someone mentioned in an answer). *Known values* = the applicant's own or a referee's name from the form.", "",
              "| Group | Detector: full | partial | missed | Known values: full |", "|---|---|---|---|---|"]
        groups: dict[str, list[dict]] = defaultdict(list)
        for n in res.names:
            groups[n["group"]].append(n)
        for g, ns in groups.items():
            c = lambda k, s: sum(n[k] == s for n in ns)  # noqa: E731
            L.append(f"| {g} | {c('detector', 'full')}/{len(ns)} | {c('detector', 'partial')} | {c('detector', 'missed')} | "
                     f"{c('known', 'full')}/{len(ns)} |")
        misses = [n for n in res.names if n["detector"] != "full" or n["known"] != "full"]
        if misses:
            L += ["", "Not fully covered:", "", "| Sentence | Detector only | Known values |", "|---|---|---|"]
            L += [f"| {n['sentence']} | {n['detector']} | {n['known']} |" for n in misses]
        L.append("")

    if res.twins:
        L += ["## Findings before and after redaction (twin set)", "",
              "Same facts written polished (N01), plain (N08) and in second-language English (N09). "
              "Assessed by the **offline keyword stub, not an LLM**, so this checks that redaction does not "
              "remove anything the rules and quote checks need; it says nothing about a real model.", "",
              "| Case | Rule checks | Changed by redaction | Matches answer key (before → after) |", "|---|---|---|---|"]
        diffs = []
        for code, r in res.twins.items():
            ch = [k for k in r["before"] if r["before"][k] != r["after"].get(k)]
            diffs += [(code, k, r["before"][k], r["after"].get(k), r["expected"].get(k)) for k in ch]
            ok_b = sum(r["before"][k] == r["expected"].get(k) for k in r["before"])
            ok_a = sum(r["after"].get(k) == r["expected"].get(k) for k in r["before"])
            L.append(f"| {code} | {len(r['before'])} | {len(ch)} | {ok_b} → {ok_a} |")
        codes = list(res.twins)
        twin_diff = [k for k in res.twins[codes[0]]["after"]
                     if len({res.twins[c]["after"].get(k) for c in codes}) > 1]
        L += ["", f"Twin consistency after redaction: {len(twin_diff)} rule(s) differ across the three styles"
              + (f" ({', '.join(twin_diff)})." if twin_diff else ".")]
        if diffs:
            L += ["", "| Case | Rule | Before | After | Answer key |", "|---|---|---|---|---|"]
            L += [f"| {c} | {k} | {b} | {a} | {e} |" for c, k, b, a, e in diffs]
        L.append("")

    L += ["## Known limits", "",
          "- Names in free text are found by patterns and a statistical model; names from some cultures, single names "
          "and unusual orders can be missed (see the tables above). Known values (form fields) are the safety net only "
          "for the applicant and named referees.",
          "- Leak scanning catches what it can recognise (emails, phones, long numbers, known values, anything already "
          "redacted elsewhere). It cannot catch an unknown name that the detector also missed.",
          "- Free text can identify someone indirectly (a rare job, a small town, a family story) without any value above.",
          "- Scanned documents, images, signatures, handwriting and headshots are not read (no OCR); they are flagged "
          "for manual review and never sent to the AI.",
          "- This set is small and hand-made, and the detector was tuned while looking at it (for example the "
          "sentence-initial name fix for \"Sione Tupou\" and keeping \"NT\"). It is not a held-out test, so these "
          "numbers are optimistic. Results on real applications will be lower.",
          "- Non-English text is over-redacted: the English NER model tags ordinary words in other languages as names "
          "(see N06). The rule is to redact when unsure, so meaning is lost rather than privacy.", ""]
    return "\n".join(L)


def run(*, twins: bool = True, names: bool = True) -> EvalResults:
    t0 = time.monotonic()
    detector = Detector(default_config())
    res = EvalResults()
    evaluate_cases(all_cases(), res, detector)
    if names:
        evaluate_names(res, detector)
    if twins:
        res.twins = evaluate_twins()
    res.seconds = time.monotonic() - t0
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=REPORT, help=f"markdown report path (default {REPORT})")
    parser.add_argument("--no-twins", action="store_true", help="skip the findings-stability check")
    parser.add_argument("--no-names", action="store_true", help="skip the name-recall-by-culture table")
    args = parser.parse_args()

    res = run(twins=not args.no_twins, names=not args.no_names)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown(res), encoding="utf-8")
    present = [v for v in res.values if v.present]
    leaks = [v for v in present if v.leaked]
    print(f"Recall {pct(len(present) - len(leaks), len(present))} | reaching LLM {sum(v.exposed for v in leaks)} | "
          f"over-redaction {pct(sum(k.removed for k in res.keeps), len(res.keeps))} | "
          f"unexpected redactions {sum(not u[3] for u in res.unlabelled)}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
