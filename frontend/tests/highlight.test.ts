// Run with: npm test   (Node's built-in test runner; no extra packages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { COLLAPSE_WORDS, collapsedBlocks, compareValues, forMode, highlightsFor, notesOf, resolveTarget, searchedSections, sectionAround, segment, toBlocks, whereIs } from "../src/screens/review/highlight.ts";
import { canSave, validMark } from "../src/screens/review/marks.ts";

const span = (source: string, start: number, end: number) => ({ source, start, end, text: "" });
const finding = (over: Record<string, unknown>) => ({ supporting_quotes_restored: [], ai_summaries_restored: [], ...over }) as never;

test("segment: highlights land on the right characters", () => {
  const text = "I lead the club. I help at the clinic.";
  const segs = segment(text, [{ id: "a", start: 17, end: 38, kind: "quote", noteId: "q0" }]);
  assert.deepEqual(segs.map((s) => s.text), ["I lead the club. ", "I help at the clinic."]);
  assert.equal(segs[1].highlight?.noteId, "q0");
  assert.equal(segs.map((s) => s.text).join(""), text);
});

test("segment: out-of-range, empty and overlapping spans never produce a wrong highlight", () => {
  const text = "0123456789";
  const segs = segment(text, [
    { id: "bad1", start: -1, end: 3, kind: "quote" }, { id: "bad2", start: 4, end: 99, kind: "quote" }, { id: "empty", start: 5, end: 5, kind: "quote" },
    { id: "ok", start: 2, end: 6, kind: "quote" }, { id: "overlap", start: 4, end: 8, kind: "summary" },
  ]);
  assert.deepEqual(segs.filter((s) => s.highlight).map((s) => s.highlight!.id), ["ok"]);
  assert.equal(segs.map((s) => s.text).join(""), text);
});

test("blocks: labelled lines, wrapped paragraphs and page markers", () => {
  const text = "[page 1]\nHEAD\nTo whom it may\nconcern,\n\nName: A\nPosition: B\n[page 2]\nNext page";
  const b = toBlocks(text);
  assert.deepEqual(b.map((x) => x.kind), ["page", "para", "para", "para", "page", "para"]);
  assert.equal(text.slice(b[1].start, b[1].end), "HEAD\nTo whom it may\nconcern,");
  assert.equal(text.slice(b[2].start, b[2].end), "Name: A");
  assert.equal(b[5].page, 2);
});

test("sectionAround: a section with context, not the whole text", () => {
  const text = Array.from({ length: 10 }, (_, i) => `field_${i}: value ${i}`).join("\n");
  const blocks = toBlocks(text);
  const at = text.indexOf("value 5");
  const sec = sectionAround(blocks, at, at + 7, 1);
  assert.deepEqual(sec.map((b) => text.slice(b.start, b.start + 7)), ["field_4", "field_5", "field_6"]);
});

test("notes: a verified, located quote is a quote and is highlighted", () => {
  const n = notesOf(finding({ supporting_quotes_restored: [{ quote: "Q", verified: true, span: span("application_text", 5, 9) }] }));
  assert.equal(n.others[0].state, "verified");
  assert.deepEqual(highlightsFor(n, "application_text").map((h) => [h.kind, h.start, h.end]), [["quote", 5, 9]]);
});

test("notes: a quote that failed verification is 'Could not verify', not a quote, and is never highlighted", () => {
  const n = notesOf(finding({ supporting_quotes_restored: [{ quote: "invented", verified: false, span: span("application_text", 1, 4) }] }));
  assert.equal(n.quotes.length, 0);
  assert.equal(n.unverified.length, 1);
  assert.equal(highlightsFor(n, "application_text").length, 0);
});

test("notes: a verified quote with no position says it could not be located and is not highlighted", () => {
  const n = notesOf(finding({ supporting_quotes_restored: [{ quote: "Q", verified: true, span: null }] }));
  assert.equal(n.quotes[0].state, "unlocated");
  assert.equal(highlightsFor(n, "application_text").length, 0);
});

test("notes: a summary with no verified source passage is listed as unlinked and never shown as a note (old slots test)", () => {
  const n = notesOf(finding({ ai_summaries_restored: [
    { text: "no source", linked: false, passages: [{ quote: "x", verified: false, span: null }] },
    { text: "no passages at all", linked: true, passages: [] },
    { text: "good", linked: true, passages: [{ quote: "y", verified: true, span: span("application_text", 2, 8) }] },
  ] }));
  assert.deepEqual(n.bullets.map((s) => s.text), ["good"]);
  assert.deepEqual(n.unlinked.map((s) => s.text), ["no source", "no passages at all"]);
  assert.equal(highlightsFor(n, "application_text")[0].kind, "summary");
});

test("notes: a linked summary whose passage cannot be positioned is not highlighted", () => {
  const n = notesOf(finding({ ai_summaries_restored: [{ text: "s", linked: true, passages: [{ quote: "y", verified: true, span: null }] }] }));
  assert.equal(n.bullets[0].state, "unlocated");
  assert.equal(highlightsFor(n, "application_text").length, 0);
});

test("notes: quote slots are capped at 3 and bullets at 6", () => {
  const many = Array.from({ length: 6 }, (_, i) => ({ quote: `q${i}`, verified: true, span: span("application_text", i * 10, i * 10 + 5) }));
  assert.equal(notesOf(finding({ supporting_quotes_restored: many })).quotes.length, 3);
  const bullets = Array.from({ length: 9 }, (_, i) => ({ text: `b${i}`, linked: true, passages: [{ quote: "x", verified: true, span: span("application_text", i * 10, i * 10 + 5) }] }));
  const n = notesOf(finding({ ai_summaries_restored: bullets }));
  assert.equal(n.bullets.length, 6);
  assert.equal(n.moreBullets, 3);
});

test("notes: no field carries a score, rating or strength", () => {
  const n = notesOf(finding({ supporting_quotes_restored: [{ quote: "Q", verified: true, span: span("a", 0, 1) }] }));
  assert.ok(!/score|rating|strength|rank/i.test(Object.keys(n.quotes[0]).join(",")));
});

test("resolveTarget: quote span first, then the typed form field, then the document value", () => {
  const form = "applicant_name: Soraya\ncourse_name: Bachelor of Nursing\ncourse_start_date: 2026-11-16";
  const detail = {
    source_texts: { application_text: { label: "Application form", text: form }, "document:d1": { label: "CoE", text: "Course: Bachelor of Nursing\nCourse Start Date: 16 November 2026" } },
    documents: [{ id: "d1", declared_type: "coe", detected_type: "coe", extracted_fields: { course_start_date: "16 November 2026" } }],
  } as never;
  const withSpan = resolveTarget({ evidence_span: span("application_text", 0, 5), rule_sources: [] } as never, detail);
  assert.deepEqual([withSpan?.how, withSpan?.start], ["quote", 0]);
  const field = resolveTarget({ rule_sources: [{ typed: "course_start_date", document_type: null, document_field: null }] } as never, detail)!;
  assert.equal(form.slice(field.start, field.end), "2026-11-16");
  const doc = resolveTarget({ rule_sources: [{ typed: null, document_type: "coe", document_field: "course_start_date" }] } as never, detail)!;
  assert.equal(doc.source, "document:d1");
  assert.equal(resolveTarget({ rule_sources: [] } as never, detail), null);
  assert.deepEqual(searchedSections(detail), ["Application form", "CoE"]);
});

test("compareValues: names, dates and empty values", () => {
  assert.equal(compareValues("Soraya Pemberton", "PEMBERTON, Soraya"), "match");
  assert.equal(compareValues("Imelda Fairweather", "Imelda Fairchild"), "differs");
  assert.equal(compareValues("2026-11-16", "16 November 2026"), "match");
  assert.equal(compareValues("2026-11-16", "30 November 2026"), "differs");
  assert.equal(compareValues("", "x"), "not_compared");
  assert.equal(compareValues("x", null), "not_compared");
});

test("highlights: a quote that is already a bullet's passage is not highlighted twice; the bullet's highlight is the one shown", () => {
  const same = span("application_text", 5, 20);
  const n = notesOf(finding({
    supporting_quotes_restored: [{ quote: "Q", verified: true, span: same }],
    ai_summaries_restored: [{ text: "S", linked: true, passages: [{ quote: "Q", verified: true, span: same }] }],
  }));
  const kinds = highlightsFor(n, "application_text", null).map((h) => h.kind);
  assert.deepEqual(kinds, ["summary"]);
});

test("bullets follow the document, not importance: sorted by source order then position", () => {
  const n = notesOf(finding({ ai_summaries_restored: [
    { text: "later in the form", linked: true, passages: [{ quote: "a", verified: true, span: span("application_text", 90, 99) }] },
    { text: "in the letter", linked: true, passages: [{ quote: "b", verified: true, span: span("document:d1", 5, 9) }] },
    { text: "early in the form", linked: true, passages: [{ quote: "c", verified: true, span: span("application_text", 10, 20) }] },
  ] }), ["application_text", "document:d1"]);
  assert.deepEqual(n.bullets.map((b) => b.text), ["early in the form", "later in the form", "in the letter"]);
});

test("other passages: a quote that is already a bullet's passage is not shown twice; one other passage gets no heading slot", () => {
  const same = span("application_text", 5, 20);
  const n = notesOf(finding({
    supporting_quotes_restored: [{ quote: "Q1", verified: true, span: same }, { quote: "Q2", verified: true, span: span("application_text", 40, 50) }],
    ai_summaries_restored: [{ text: "S", linked: true, passages: [{ quote: "Q1", verified: true, span: same }] }],
  }));
  assert.equal(n.bullets.length, 1);
  assert.deepEqual(n.others.map((q) => q.text), ["Q2"]);   // exactly one: the screen shows it with no "Other passages" heading and no filler
});

test("no filler: with nothing to show there are no notes at all (not empty slots)", () => {
  const n = notesOf(finding({}));
  assert.deepEqual([n.bullets.length, n.others.length, n.unlinked.length, n.unverified.length], [0, 0, 0, 0]);
});

test("referee facts are kept apart from the summaries", () => {
  const f = finding({ supporting_quotes_restored: [
    { quote: "Referee name: A", label: "referee name", verified: true, span: span("document:d1", 0, 5) },
    { quote: "She leads", label: "about the applicant", verified: true, span: span("document:d1", 9, 12) }] });
  assert.deepEqual(notesOf(forMode(f, "referee")).others.map((q) => q.label), ["referee name"]);
  assert.deepEqual(notesOf(forMode(f, "criterion")).others.map((q) => q.label), ["about the applicant"]);
});

test("long source text collapses to the paragraphs holding a highlight; short text is shown whole", () => {
  const long = `field_a: ${"word ".repeat(COLLAPSE_WORDS)}\nfield_b: the passage here\nfield_c: ${"more ".repeat(20)}`;
  const at = long.indexOf("the passage");
  const v = collapsedBlocks(long, [{ id: "h", start: at, end: at + 11, kind: "quote" }]);
  assert.ok(v.collapsible && v.words > COLLAPSE_WORDS);
  assert.deepEqual(v.blocks.map((b) => long.slice(b.start, b.start + 7)), ["field_b"]);
  const short = collapsedBlocks("field_a: hello\nfield_b: there", []);
  assert.equal(short.collapsible, false);
  assert.equal(short.blocks.length, 2);
});

test("whereIs gives the page and paragraph of a passage", () => {
  const text = "[page 1]\nfield_a: one\n[page 2]\nfield_b: two\nfield_c: three";
  assert.deepEqual(whereIs(text, text.indexOf("three")), { page: 2, paragraph: 3 });
  assert.equal(whereIs(text, 0), null);
});

test("marking rules: a whole number 0 to 100 with a reason, or Not assessed; nothing is pre-filled", () => {
  assert.equal(validMark("0"), 0);
  assert.equal(validMark("100"), 100);
  for (const bad of ["", "101", "-1", "50.5", "abc", " ", "1e2"]) assert.equal(validMark(bad), null, bad);
  assert.equal(canSave("70", "", false), false);          // a mark needs a reason
  assert.equal(canSave("70", "   ", false), false);
  assert.equal(canSave("70", "Strong prize record", false), true);
  assert.equal(canSave("", "reason", false), false);      // no mark chosen
  assert.equal(canSave("", "", true), true);              // Not assessed needs no mark
});
