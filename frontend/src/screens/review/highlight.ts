// Pure logic for the review screen's text viewer and notes. No React, no network, so it can be tested on its own.
//
// Positions always come from the backend's exact span mapping (redacted text -> the applicant's original words).
// If a passage has no position, nothing here guesses one: the note says it could not be located.
import type { Detail, Finding, TextSpan } from "../../api";

// ---------------------------------------------------------------- highlighting

export type HighlightKind = "quote" | "summary" | "focus";
export interface Highlight { id: string; start: number; end: number; kind: HighlightKind; noteId?: string }
export interface Segment { text: string; start: number; end: number; highlight?: Highlight }

/** Split `text` into plain and highlighted pieces. Out-of-range or empty spans are ignored; overlaps keep the earlier span. */
export function segment(text: string, highlights: Highlight[]): Segment[] {
  const valid = highlights
    .filter((h) => Number.isInteger(h.start) && Number.isInteger(h.end) && h.start >= 0 && h.end <= text.length && h.end > h.start)
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Segment[] = [];
  let pos = 0;
  for (const h of valid) {
    if (h.start < pos) continue; // overlaps the previous highlight
    if (h.start > pos) out.push({ text: text.slice(pos, h.start), start: pos, end: h.start });
    out.push({ text: text.slice(h.start, h.end), start: h.start, end: h.end, highlight: h });
    pos = h.end;
  }
  if (pos < text.length) out.push({ text: text.slice(pos), start: pos, end: text.length });
  return out;
}

// ---------------------------------------------------------------- paragraphs and page markers

export interface Block { start: number; end: number; kind: "page" | "para"; page?: number; number?: number }
const PAGE = /^\[page (\d+)\]$/i;
const LABELLED = /^[A-Za-z][A-Za-z0-9_ ()'’\-/]{1,60}:\s/;

/** Paragraph and page-marker blocks with their offsets. A labelled line ("Referee name: ...") starts a new paragraph; a blank
 *  line or a page marker ends one; any other line continues the paragraph it follows. Offsets index the original text. */
export function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let page = 1;
  let n = 0;
  let open: Block | null = null;
  let pos = 0;
  for (const line of text.split("\n")) {
    const start = pos;
    const end = pos + line.length;
    pos = end + 1;
    const m = line.trim().match(PAGE);
    if (m) {
      open = null;
      page = Number(m[1]);
      blocks.push({ start, end, kind: "page", page });
    } else if (!line.trim()) {
      open = null;
    } else if (open && !LABELLED.test(line)) {
      open.end = end;
    } else {
      open = { start, end, kind: "para", page, number: ++n };
      blocks.push(open);
    }
  }
  return blocks;
}

/** The block(s) holding [start, end) plus `context` paragraphs either side: a section, not the whole text. */
export function sectionAround(blocks: Block[], start: number, end: number, context = 2): Block[] {
  const paras = blocks.filter((b) => b.kind === "para");
  const first = paras.findIndex((b) => b.end > start);
  if (first < 0) return [];
  let last = first;
  while (last + 1 < paras.length && paras[last + 1].start < end) last++;
  return paras.slice(Math.max(0, first - context), Math.min(paras.length, last + 1 + context));
}

// ---------------------------------------------------------------- AI notes

export interface Located { source: string; start: number; end: number }
export interface QuoteNote {
  id: string; kind: "quote"; text: string; label?: string; at: Located | null;
  state: "verified" | "unlocated" | "unverified";
}
export interface SummaryNote {
  id: string; kind: "summary"; text: string; at: Located[]; sources: string[];
  state: "linked" | "unlocated" | "unlinked";
}
export interface Notes {
  quotes: QuoteNote[];          // verified quotes, at most MAX_QUOTES slots (shown even when their position is unknown)
  summaries: SummaryNote[];     // summaries linked to the text, at most MAX_SUMMARIES slots
  unverified: QuoteNote[];      // "Could not verify": never shown as a quote, never highlighted
  unlinked: SummaryNote[];      // "Could not be linked to the text": a summary with no source passage
  moreQuotes: number;           // verified quotes beyond the slots
}
export const MAX_QUOTES = 3;
export const MAX_SUMMARIES = 2;

const here = (s: TextSpan | null | undefined): Located | null => (s ? { source: s.source, start: s.start, end: s.end } : null);

/** Sort the finding's passages into note slots by what code could establish. Nothing here scores or ranks them. */
export function notesOf(f: Pick<Finding, "supporting_quotes_restored" | "ai_summaries_restored">): Notes {
  const quotes: QuoteNote[] = [];
  const unverified: QuoteNote[] = [];
  (f.supporting_quotes_restored ?? []).forEach((q, i) => {
    const note: QuoteNote = { id: `q${i}`, kind: "quote", text: q.quote, label: q.label, at: here(q.span), state: "unverified" };
    if (!q.verified) unverified.push(note);
    else { note.state = note.at ? "verified" : "unlocated"; quotes.push(note); }
  });
  // "About the applicant" passages come before bare field lines such as a referee's name, so the 3 slots read well.
  quotes.sort((a, b) => Number((b.label ?? "") === "about the applicant") - Number((a.label ?? "") === "about the applicant"));
  const summaries: SummaryNote[] = [];
  const unlinked: SummaryNote[] = [];
  (f.ai_summaries_restored ?? []).forEach((s, i) => {
    const verified = s.passages.filter((p) => p.verified);
    const at = verified.map((p) => here(p.span)).filter((x): x is Located => x !== null);
    const note: SummaryNote = { id: `s${i}`, kind: "summary", text: s.text ?? "", at, sources: [...new Set(at.map((a) => a.source))], state: "linked" };
    if (!s.linked || verified.length === 0) { note.state = "unlinked"; note.at = []; unlinked.push(note); }
    else { note.state = at.length ? "linked" : "unlocated"; summaries.push(note); }
  });
  return {
    quotes: quotes.slice(0, MAX_QUOTES), summaries: summaries.slice(0, MAX_SUMMARIES), unverified, unlinked,
    moreQuotes: Math.max(0, quotes.length - MAX_QUOTES),
  };
}

/** The highlights for one source: verified quotes and linked summary passages, never failed or unlinked notes. */
export function highlightsFor(notes: Notes, source: string, selected?: string | null): Highlight[] {
  const out: Highlight[] = [];
  for (const q of notes.quotes) if (q.at && q.at.source === source) out.push({ id: `${q.id}`, noteId: q.id, start: q.at.start, end: q.at.end, kind: "quote" });
  for (const s of notes.summaries) s.at.forEach((a, i) => { if (a.source === source) out.push({ id: `${s.id}.${i}`, noteId: s.id, start: a.start, end: a.end, kind: "summary" }); });
  // The selected note's passage wins where two notes point at the same words (a quote and a summary of it).
  return selected ? [...out.filter((h) => h.noteId === selected), ...out.filter((h) => h.noteId !== selected)] : out;
}

/** Sources (tabs) the notes point into, the form first. With no located notes at all, just the form. */
export function sourcesOf(notes: Notes, form = "application_text"): string[] {
  const all = new Set<string>();
  for (const q of notes.quotes) if (q.at) all.add(q.at.source);
  for (const s of notes.summaries) s.sources.forEach((x) => all.add(x));
  if (all.size === 0) return [form];
  return [...all].sort((a, b) => Number(b === form) - Number(a === form));
}

// ---------------------------------------------------------------- "Show the application text"

export interface Target { source: string; start: number; end: number; how: "quote" | "field" | "document" }

/** The exact passage or field a finding came from, or null when nothing in the text covers it. */
export function resolveTarget(f: Finding, detail: Pick<Detail, "documents" | "source_texts">): Target | null {
  const texts = detail.source_texts ?? {};
  const span = f.evidence_span ?? (f.supporting_quotes_restored ?? []).find((q) => q.verified && q.span)?.span ?? null;
  if (span && texts[span.source]) return { source: span.source, start: span.start, end: span.end, how: "quote" };
  const form = texts["application_text"]?.text ?? "";
  for (const r of f.rule_sources ?? []) {
    if (!r.typed) continue;
    const m = new RegExp(`^${r.typed}: `, "m").exec(form);
    if (m) {
      const start = m.index + m[0].length;
      const nl = form.indexOf("\n", start);
      return { source: "application_text", start, end: nl < 0 ? form.length : nl, how: "field" };
    }
  }
  for (const r of f.rule_sources ?? []) {
    if (!r.document_type || !r.document_field) continue;
    const doc = detail.documents.find((d) => d.declared_type === r.document_type || d.detected_type === r.document_type);
    const value = doc?.extracted_fields?.[r.document_field];
    const key = doc ? `document:${doc.id}` : "";
    const at = value && texts[key] ? texts[key].text.indexOf(value) : -1;
    if (at >= 0) return { source: key, start: at, end: at + value!.length, how: "document" };
  }
  return null;
}

/** What was searched, for "No matching text found". */
export function searchedSections(detail: Pick<Detail, "documents" | "source_texts">): string[] {
  const texts = detail.source_texts ?? {};
  const out = Object.values(texts).map((t) => t.label);
  return out.length ? out : ["Application form", ...detail.documents.map((d) => d.file_name)];
}

// ---------------------------------------------------------------- typed value vs document value

export type Match = "match" | "differs" | "not_compared";
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

export function parseDay(value: string): string | null {
  const iso = value.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
  const d = value.match(/(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s+(\d{4})/);
  if (d) {
    const m = MONTHS.findIndex((x) => x.startsWith(d[2].toLowerCase().slice(0, 3)));
    if (m >= 0) return `${d[3]}-${String(m + 1).padStart(2, "0")}-${d[1].padStart(2, "0")}`;
  }
  return null;
}

const words = (s: string) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean).sort().join(" ");

/** Compare what was typed with what a document says. Only a clear difference is called a difference. */
export function compareValues(typed: string | null | undefined, doc: string | null | undefined): Match {
  if (!typed || !typed.trim() || !doc || !doc.trim()) return "not_compared";
  const a = parseDay(typed);
  const b = parseDay(doc);
  if (a && b) return a === b ? "match" : "differs";
  return words(typed) === words(doc) ? "match" : "differs";
}
