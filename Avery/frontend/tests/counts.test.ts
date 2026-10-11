// The numbers on the Assessment screen come from one function, so the header, the 3a / 3b switch and the cards cannot disagree.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { assessmentCounts, isDecided, missingFor } from "../src/counts.ts";

const rule = (code: string, over: Record<string, unknown> = {}) => ({ id: code, rule_code: code, section: "eligibility", latest_review: null, merit_mark: null, ...over });
const reviewed = { action: "confirm", final_status: "Met" };
const detail = (findings: unknown[], overview: unknown[] = [], linked: unknown[] = []) =>
  ({ findings, consistency: { overview }, linked_applications: linked }) as never;

test("a rule is decided by an officer review; asking the applicant is not a decision; a merit criterion by a mark", () => {
  assert.equal(isDecided(rule("S1") as never), false);
  assert.equal(isDecided(rule("S1", { latest_review: reviewed }) as never), true);
  assert.equal(isDecided(rule("S1", { latest_review: { action: "ask_applicant" } }) as never), false);
  assert.equal(isDecided(rule("M1", { section: "merit" }) as never), false);
  assert.equal(isDecided(rule("M1", { section: "merit", merit_mark: { mark: 50, not_assessed: false } }) as never), true);
});

const sample = detail(
  [rule("S1", { latest_review: reviewed }), rule("S2"), rule("D1", { section: "documents" }), rule("M1", { section: "merit" }), rule("M2", { section: "merit", merit_mark: { mark: null, not_assessed: true } })],
  [{ result: "Differs", decision: "Not decided" }, { result: "Differs", decision: "Dismissed" }, { result: "Cannot compare", decision: "Not decided" }, { result: "Consistent", decision: "Not needed" }],
  [{ application_id: "x" }],
);

test("the counts add up the same way everywhere", () => {
  const c = assessmentCounts(sample);
  assert.equal(c.eligibility.total, 3);
  assert.equal(c.eligibility.decided + c.eligibility.undecided, c.eligibility.total);
  assert.equal(c.merit.marked + c.merit.unmarked, c.merit.total);
  assert.equal(c.decided, c.eligibility.decided + c.merit.marked);
  assert.equal(c.total, c.eligibility.total + c.merit.total);
  assert.deepEqual(c.consistency, { toCheck: 1, differs: 2, cannotCompare: 1 });
  assert.equal(c.linked, 1);
});

test("the sentence under Continue quotes the same numbers, and consistency or linked applications never block", () => {
  const c = assessmentCounts(sample);
  assert.equal(missingFor(c, "3a"), "2 eligibility findings need an officer decision.");
  assert.equal(missingFor(c, "3b"), "1 merit criterion needs a mark or Not assessed.");
  const done = assessmentCounts(detail([rule("S1", { latest_review: reviewed }), rule("M1", { section: "merit", merit_mark: { mark: 70, not_assessed: false } })],
    [{ result: "Differs", decision: "Not decided" }], [{ application_id: "x" }]));
  assert.equal(missingFor(done, "3a"), null);
  assert.equal(missingFor(done, "3b"), null);   // an open consistency difference and a linked application do not block
});

test("the header, switch and cards read the counts from src/counts.ts, not their own arithmetic", () => {
  for (const file of ["../src/screens/Application.tsx", "../src/screens/Review.tsx"]) {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.match(src, /counts/);
    assert.doesNotMatch(src, /\.filter\(isDecided\)\.length/, `${file} must not count decided items itself`);
  }
  assert.match(readFileSync(new URL("../src/screens/Application.tsx", import.meta.url), "utf8"), /assessmentCounts\(detail\)/);
});
