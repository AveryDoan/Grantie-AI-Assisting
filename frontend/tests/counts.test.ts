// The sub-step counts come from ONE place, the backend's step 3 "substeps" and "progress" (tests/test_substeps.py checks
// they agree with the rule engine). Here: the pill, the section card and the header all show that same number, and nothing
// in the browser recounts them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isDecided } from "../src/counts.ts";

const rule = (code: string, over: Record<string, unknown> = {}) => ({ id: code, rule_code: code, section: "eligibility", latest_review: null, merit_mark: null, ...over });
const src = (f: string) => readFileSync(new URL(f, import.meta.url), "utf8");

test("a rule is decided by an officer review; asking the applicant is not a decision; a merit criterion by a mark", () => {
  assert.equal(isDecided(rule("S1") as never), false);
  assert.equal(isDecided(rule("S1", { latest_review: { action: "confirm" } }) as never), true);
  assert.equal(isDecided(rule("S1", { latest_review: { action: "ask_applicant" } }) as never), false);
  assert.equal(isDecided(rule("M1", { section: "merit" }) as never), false);
  assert.equal(isDecided(rule("M1", { section: "merit", merit_mark: { mark: 50, not_assessed: false } }) as never), true);
});

test("the pill, the section card and the header read the same backend numbers", () => {
  const review = src("../src/screens/Review.tsx");
  const bar = src("../src/screens/steps/SubStepBar.tsx");
  const application = src("../src/screens/Application.tsx");
  // the sub-step card (the pill) prints the backend's count for that sub-step, and Review passes the backend's list straight in
  assert.match(bar, /data-count=\{s\.count\}/, "the sub-step card shows the sub-step count");
  assert.match(bar, /\{s\.count > 0 && <span className="ss-count"/, "the count badge is the same number");
  assert.match(review, /<SubStepBar subs=\{subs\}/, "the cards get the backend's sub-steps unchanged");
  // the header shows the backend's progress, which is the sum of the blocking sub-step counts
  assert.match(application, /step3\?\.progress/);
  assert.match(application, /step3\?\.substeps/);
  for (const [name, text] of [["Review.tsx", review], ["Application.tsx", application]] as const) {
    assert.doesNotMatch(text, /\.filter\(isDecided\)\.length|assessmentCounts|missingFor/, `${name} must not count on its own`);
  }
});

test("no 3a or 3b remains", () => {
  for (const f of ["../src/screens/Review.tsx", "../src/screens/Application.tsx", "../src/App.tsx", "../src/counts.ts"]) {
    assert.doesNotMatch(src(f), /["'`]3a["'`]|["'`]3b["'`]|3a Eligibility|3b Merit/, f);
  }
});
