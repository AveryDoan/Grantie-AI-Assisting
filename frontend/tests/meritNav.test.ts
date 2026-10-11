// Summary -> "Show in document" -> step through quotes -> back. Pure navigation, tested without a browser.
import { test } from "node:test";
import assert from "node:assert/strict";
import { documentTabs, focusAtQuote, focusItem, focusTheme, itemForQuote, move, quotesByDocument, stepsForItem } from "../src/screens/review/meritNav.ts";

const q = (source: string, start: number, name: string) => ({ source, name, file_name: `${name}.pdf`, text: `quote at ${start}`, span: { source, start, end: start + 5, text: "" } });
const form = q("application_text", 10, "Application form");
const ref1 = q("document:r1", 40, "Referee letter 1");
const cv = q("document:cv", 5, "Resume");
const item1 = { item_id: "b1", text: "The form and a referee give a GPA of 3.84.", differs: false, quotes: [form, ref1, form] };
const item2 = { item_id: "b2", text: "The resume lists the same award.", differs: false, quotes: [cv] };
const summary = {
  version: 1, status: "ok", error: null, generated_at: null, label: "Academic merit", overview: ["Two sentences."],
  themes: [{ id: "grades", label: "Grades", from: [form, ref1], items: [item1] }, { id: "awards", label: "Awards", from: [cv], items: [item2] }],
  other_passages: [], all_quotes: [form, ref1, cv], could_not_verify: [],
  coverage: { with_matches: 3, document_count: 3, documents: [form, ref1, cv].map((d) => ({ ...d, passages: 1, readable: true })) }, versions: [1],
} as never;

test("Show in document opens the bullet's quotes, each once, form first", () => {
  const f = focusItem(item1 as never);
  assert.equal(f.kind, "focus");
  assert.deepEqual(f.steps.map((s) => s.quote.source), ["application_text", "document:r1"]);   // the repeated form quote is shown once
  assert.equal(f.at, 0);
});

test("Next and Previous step across documents and stop at either end", () => {
  let s = focusItem(item1 as never) as ReturnType<typeof move>;
  s = move(s, 1);
  assert.equal(s.kind === "focus" && s.steps[s.at].quote.source, "document:r1");   // the viewer switches to the referee letter
  s = move(s, 1);
  assert.equal(s.kind === "focus" && s.at, 1);                                      // no step past the last quote
  s = move(move(s, -1), -1);
  assert.equal(s.kind === "focus" && s.at, 0);
  assert.deepEqual(move({ kind: "summary" }, 1), { kind: "summary" });               // nothing moves in the summary
});

test("Show all in document walks every bullet of the theme", () => {
  assert.equal(focusTheme((summary as any).themes[0]).steps.length, stepsForItem(item1 as never).length);
});

test("a highlight leads back to the bullet it supports, at that quote", () => {
  assert.equal(itemForQuote(summary, ref1)?.item_id, "b1");
  assert.equal(itemForQuote(summary, cv)?.item_id, "b2");
  assert.equal(focusAtQuote(item1 as never, ref1).at, 1);
});

test("All quotes lists each quote once, grouped by document in reading order, with its bullet", () => {
  const groups = quotesByDocument(summary);
  assert.deepEqual(groups.map((g) => g.doc.name), ["Application form", "Referee letter 1", "Resume"]);
  assert.equal(groups[0].quotes.length, 1);
  assert.equal(groups[2].quotes[0].item.item_id, "b2");
});

test("one tab per document, plainly named", () => {
  const tabs = documentTabs(summary);
  assert.deepEqual(tabs.map((t) => t.name), ["Application form", "Referee letter 1", "Resume"]);
  assert.equal(new Set(tabs.map((t) => t.source)).size, tabs.length);
});
