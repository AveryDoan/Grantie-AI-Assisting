// Sub-step 3.3, AI-use check: the screen shows the policy, the exact caveat, neutral results only, and never a percentage,
// a red colour, accusing words or sentence highlighting.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const raw = readFileSync(new URL("../src/screens/review/AiUseCheck.tsx", import.meta.url), "utf8");
const src = raw.split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");   // the code, not its comments
const css = readFileSync(new URL("../src/theme.css", import.meta.url), "utf8");

test("the caveat is shown word for word", () => {
  assert.ok(src.includes("This check is a weak signal. It was trained on older AI text and cannot tell allowed use (spelling, grammar, organising ideas) from disallowed use (writing, improving, paraphrasing). People who write plainly or in a second language are often marked 'AI-like' by tools like this. It is a prompt for a conversation, not evidence."));
  assert.match(src, /I have followed these rules\./);
});

test("no percentages, no scores, no red, no accusing words", () => {
  assert.doesNotMatch(src, /%|\bmean\b|\.scores|toFixed|percent/i);
  assert.doesNotMatch(src, /st-notmet|var\(--red|brand-red|warn-notice/);
  const block = css.slice(css.indexOf("3.3 AI-use check"));
  assert.doesNotMatch(block, /st-notmet|--red|brand-red/);
  for (const w of ["fraud", "fake", "cheat", "suspicious", "dishonest"]) assert.doesNotMatch(src.toLowerCase(), new RegExp(w));
});

test("three officer actions, and the request goes through the evidence-request flow for approval", () => {
  for (const a of ["No action needed", "Add a private note", "Ask the applicant to explain in their own words"]) assert.ok(src.includes(a), a);
  assert.match(src, /api\.editRequest/);
  assert.match(src, /api\.sendRequest/);
});
