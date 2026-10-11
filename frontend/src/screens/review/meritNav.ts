// Moving through the evidence behind a merit summary line: the quotes of one bullet (or a whole theme), in order, and back
// from a highlight to the line it supports. Pure functions, so they are tested on their own (tests/meritNav.test.ts).
import type { MeritItem, MeritQuote, MeritSummary, MeritTheme } from "../../api";

export interface Step { quote: MeritQuote; itemId: string }
export type LaneState =
  | { kind: "summary" }
  | { kind: "focus"; title: string; steps: Step[]; at: number }
  | { kind: "all" } | { kind: "other" } | { kind: "failed" };

export const quoteKey = (q: Pick<MeritQuote, "source" | "span" | "text">) => `${q.source}@${q.span ? q.span.start : q.text ?? ""}`;

/** The quotes behind one bullet, each once, in the order the bullet cites them (form first, then each document). */
export function stepsForItem(it: MeritItem): Step[] {
  const seen = new Set<string>();
  return it.quotes.filter((q) => { const k = quoteKey(q); if (seen.has(k)) return false; seen.add(k); return true; }).map((quote) => ({ quote, itemId: it.item_id }));
}

/** "Show all in document" for a theme: every bullet's quotes, bullet by bullet. */
export function stepsForTheme(t: MeritTheme): Step[] {
  return t.items.flatMap(stepsForItem);
}

export type Focus = Extract<LaneState, { kind: "focus" }>;
export const focusItem = (it: MeritItem, at = 0): Focus => ({ kind: "focus", title: it.text ?? "", steps: stepsForItem(it), at });
export const focusTheme = (t: MeritTheme): Focus => ({ kind: "focus", title: t.label, steps: stepsForTheme(t), at: 0 });

/** Open a bullet at one of its quotes (from "All quotes"). */
export function focusAtQuote(it: MeritItem, q: Pick<MeritQuote, "source" | "span" | "text">): Focus {
  const f = focusItem(it);
  return { ...f, at: Math.max(0, f.steps.findIndex((s) => quoteKey(s.quote) === quoteKey(q))) };
}

/** Previous / Next, never past either end. */
export function move(state: LaneState, delta: number): LaneState {
  if (state.kind !== "focus" || state.steps.length === 0) return state;
  return { ...state, at: Math.max(0, Math.min(state.steps.length - 1, state.at + delta)) };
}

/** The bullet a quote supports (a click on a highlight opens that bullet). */
export function itemForQuote(summary: MeritSummary, q: Pick<MeritQuote, "source" | "span" | "text">): MeritItem | null {
  const k = quoteKey(q);
  for (const t of summary.themes) for (const it of t.items) if (it.quotes.some((x) => quoteKey(x) === k)) return it;
  return null;
}

/** Every verified quote once, grouped by document in reading order, with the bullet it supports. No summary text. */
export function quotesByDocument(summary: MeritSummary): { doc: { source: string; name: string; file_name: string | null }; quotes: { quote: MeritQuote; item: MeritItem }[] }[] {
  const order = summary.coverage.documents.map((d) => d.source);
  const groups = new Map<string, { doc: { source: string; name: string; file_name: string | null }; quotes: { quote: MeritQuote; item: MeritItem }[] }>();
  const seen = new Set<string>();
  for (const t of summary.themes) for (const it of t.items) for (const q of it.quotes) {
    const k = quoteKey(q);
    if (seen.has(k)) continue;
    seen.add(k);
    const g = groups.get(q.source) ?? { doc: { source: q.source, name: q.name, file_name: q.file_name }, quotes: [] };
    g.quotes.push({ quote: q, item: it });
    groups.set(q.source, g);
  }
  const rank = (s: string) => { const i = order.indexOf(s); return i < 0 ? order.length : i; };
  return [...groups.values()].sort((a, b) => rank(a.doc.source) - rank(b.doc.source))
    .map((g) => ({ ...g, quotes: g.quotes.sort((a, b) => (a.quote.span?.start ?? 0) - (b.quote.span?.start ?? 0)) }));
}

/** The documents a summary points into, once each, in reading order: the viewer's tabs. */
export function documentTabs(summary: MeritSummary): { source: string; name: string; file_name: string | null }[] {
  const seen = new Set<string>();
  const out: { source: string; name: string; file_name: string | null }[] = [];
  const add = (d: { source: string; name: string; file_name: string | null }) => { if (!seen.has(d.source)) { seen.add(d.source); out.push({ source: d.source, name: d.name, file_name: d.file_name }); } };
  for (const t of summary.themes) t.from.forEach(add);
  summary.all_quotes.forEach(add);
  const order = summary.coverage.documents.map((d) => d.source);
  const rank = (s: string) => { const i = order.indexOf(s); return i < 0 ? order.length : i; };
  return out.sort((a, b) => rank(a.source) - rank(b.source));
}
