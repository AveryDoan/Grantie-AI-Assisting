// One source of truth for the counts on the Assessment screen. The header line, the 3a / 3b switch, the overview cards and the
// drawer all read these numbers, so they cannot disagree (see tests/counts.test.ts). Counts only: nothing here scores or ranks.
import type { Detail, Finding } from "./api";

/** A merit criterion is done once the officer has marked it (or set it to Not assessed); every other rule by a review. */
export function isDecided(f: Finding): boolean {
  if (f.section === "merit") return !!f.merit_mark;
  return !!f.latest_review && f.latest_review.action !== "ask_applicant";
}

export const isRule = (f: Finding) => f.section !== "merit";

export interface Counts {
  eligibility: { total: number; decided: number; undecided: number };
  merit: { total: number; marked: number; unmarked: number };
  consistency: { toCheck: number; differs: number; cannotCompare: number };
  linked: number;
  decided: number;   // all findings (rules and merit) with an officer decision or mark
  total: number;
}

export function assessmentCounts(detail: Detail): Counts {
  const findings = detail.findings;
  const rules = findings.filter(isRule);
  const merit = findings.filter((f) => !isRule(f));
  const overview = detail.consistency?.overview ?? [];
  const differs = overview.filter((r) => r.result === "Differs");
  const decidedRules = rules.filter(isDecided).length;
  const marked = merit.filter(isDecided).length;
  return {
    eligibility: { total: rules.length, decided: decidedRules, undecided: rules.length - decidedRules },
    merit: { total: merit.length, marked, unmarked: merit.length - marked },
    consistency: { toCheck: differs.filter((r) => r.decision === "Not decided").length, differs: differs.length, cannotCompare: overview.filter((r) => r.result === "Cannot compare").length },
    linked: (detail.linked_applications ?? []).length,
    decided: decidedRules + marked,
    total: findings.length,
  };
}

/** What 3a and 3b still need, in plain words. Consistency and Linked applications never appear here: they never block. */
export function missingFor(c: Counts, part: "3a" | "3b"): string | null {
  if (part === "3a") {
    const n = c.eligibility.undecided;
    return n ? `${n} eligibility ${n === 1 ? "finding needs" : "findings need"} an officer decision.` : null;
  }
  const n = c.merit.unmarked;
  return n ? `${n} merit ${n === 1 ? "criterion needs" : "criteria need"} a mark or Not assessed.` : null;
}
