// WCAG AA check on the colour pairs the interface uses (text 4.5:1, icons and large shapes 3:1). Reads the tokens, so a new
// colour that fails shows up here. Also checks that components do not hard-code colours that bypass the tokens.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/tokens.css", import.meta.url), "utf8");
const tok = (name: string): string => {
  const m = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  assert.ok(m, `token --${name} must be a hex colour`);
  return m![1];
};
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

const W = "#ffffff";
const TEXT: [string, string, string][] = [
  // [label, foreground token or hex, background token or hex]
  ["body text on page", "brand-navy", "page"], ["body text on card", "brand-navy", "card"], ["muted text on card", "muted-ink", "card"], ["muted text on page", "muted-ink", "page"],
  ["primary button", W, "brand-orange"], ["primary button hover", W, "brand-orange-dark"], ["link on card", "brand-orange", "card"],
  ["Met", "st-met", "st-met-tint"], ["Not met", "st-notmet", "st-notmet-tint"], ["Needs evidence", "st-needs", "st-needs-tint"],
  ["Unclear", "st-unclear", "st-unclear-tint"], ["Officer judgement", "st-judgement", "st-judgement-tint"],
  ["AI strip", W, "brand-teal"], ["active step pill: docs", W, "sec-docs"], ["active step pill: redaction", W, "sec-redaction"],
  ["active step pill: eligibility", W, "sec-elig"], ["active step pill: outcome", W, "sec-outcome"],
  ["section text docs", "sec-docs", "sec-docs-tint"], ["section text redaction", "sec-redaction", "sec-redaction-tint"], ["section text eligibility", "sec-elig", "sec-elig-tint"],
  ["section text merit", "sec-merit-ink", "sec-merit-tint"], ["section text consistency", "sec-consistency", "sec-consistency-tint"],
  ["section text linked", "sec-linked", "sec-linked-tint"], ["section text outcome", "sec-outcome", "sec-outcome-tint"],
  ["band text on tint", "brand-navy", "sec-docs-tint"], ["band text on merit tint", "brand-navy", "sec-merit-tint"],
];
const ICONS: [string, string, string][] = [
  ["merit icon on white", "sec-merit", "card"], ["white icon on merit solid", W, "sec-merit"], ["white icon on consistency solid", W, "sec-consistency"],
  ["white icon on linked solid", W, "sec-linked"],
];
const val = (v: string) => (v.startsWith("#") ? v : tok(v));

for (const [label, fg, bg] of TEXT) test(`text contrast AA: ${label}`, () => assert.ok(ratio(val(fg), val(bg)) >= 4.5, `${label}: ${ratio(val(fg), val(bg)).toFixed(2)}`));
for (const [label, fg, bg] of ICONS) test(`icon contrast 3:1: ${label}`, () => assert.ok(ratio(val(fg), val(bg)) >= 3, `${label}: ${ratio(val(fg), val(bg)).toFixed(2)}`));

test("the new theme and the new screens use token colours, not hard-coded hex values", () => {
  for (const f of ["../src/theme.css"]) {
    const src = readFileSync(new URL(f, import.meta.url), "utf8");
    const hits = [...src.matchAll(/#[0-9a-fA-F]{3,6}\b/g)].map((m) => m[0]).filter((h) => !["#fff", "#ffffff"].includes(h.toLowerCase()));
    // a few neutral greys for disabled and locked states are allowed; everything brand, section or status must be a token
    const allowed = new Set(["#eceff4", "#dfe3ea", "#5f6b7a", "#eef0f4", "#4b5565", "#d6d9ee", "#e6f6f3"]);
    assert.deepEqual(hits.filter((h) => !allowed.has(h.toLowerCase())), [], `${f} has hard-coded colours`);
  }
  for (const f of ["../src/screens/Application.tsx", "../src/screens/Queue.tsx", "../src/screens/Review.tsx"]) {
    assert.doesNotMatch(readFileSync(new URL(f, import.meta.url), "utf8"), /#[0-9a-fA-F]{6}\b/, `${f} must not hard-code colours`);
  }
});

test("consistency and linked applications never use the red or amber status colours", () => {
  const theme = readFileSync(new URL("../src/theme.css", import.meta.url), "utf8");
  for (const m of theme.matchAll(/(\.rv-sidecard[^{]*|\.sec-consistency[^{]*|\.sec-linked[^{]*)\{([^}]*)\}/g)) {
    assert.doesNotMatch(m[2], /st-notmet|st-needs|brand-red/, m[1]);
  }
});
