// Sorting, filtering and the "places available" line for the scores table. The default order is by reference. Any other order is an
// explicit click by the officer (and a second click reverses it, a third goes back to reference). There is no rank number anywhere:
// tied totals are equal, a missing total is "Not yet" and sorts last, never as 0.
import type { ScoreRow } from "./api";

export type SortKey = "reference" | "total" | "status" | "decision" | `m:${string}`;
export interface Sort { key: SortKey; dir: "asc" | "desc" }

const num = (v: unknown): number | null => (typeof v === "number" ? v : v === null || v === undefined || v === "NA" ? null : Number(v));
const value = (r: ScoreRow, key: SortKey): number | string | null =>
  key === "reference" ? r.reference : key === "total" ? num(r.total_exact) : key === "status" ? r.status : key === "decision" ? r.decision ?? "" : num(r.marks[key.slice(2)]);

export function sortRows(rows: ScoreRow[], sort: Sort | null): ScoreRow[] {
  const byRef = [...rows].sort((a, b) => a.reference.localeCompare(b.reference));
  if (!sort || sort.key === "reference" && sort.dir === "asc") return byRef;
  const sign = sort.dir === "asc" ? 1 : -1;
  return byRef.sort((a, b) => {
    const x = value(a, sort.key), y = value(b, sort.key);
    if (x === null && y === null) return 0;
    if (x === null) return 1;                // missing always last, in either direction
    if (y === null) return -1;
    return x === y ? 0 : (x < y ? -1 : 1) * sign;   // equal stays equal: the stable sort keeps reference order
  });
}

export function nextSort(current: Sort | null, key: SortKey): Sort | null {
  if (!current || current.key !== key) return { key, dir: "asc" };
  return current.dir === "asc" ? { key, dir: "desc" } : null;
}

export interface Filters { search: string; decision: "all" | "none" | "decided" | "selected" | "not_selected_competitive"; marks: "all" | "complete" | "waiting" }

export function filterRows(rows: ScoreRow[], f: Filters): ScoreRow[] {
  const q = f.search.trim().toLowerCase();
  return rows.filter((r) => (!q || r.reference.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q))
    && (f.decision === "all" || (f.decision === "none" ? r.decision === null : f.decision === "decided" ? r.decision !== null : r.decision === f.decision))
    && (f.marks === "all" || (f.marks === "complete" ? r.status === "complete" : r.status !== "complete")));
}

/** Index in the sorted list AFTER which the dashed "N places available" line goes. Only when sorted by total. A reference line: nothing is selected by it. */
export function placesLine(sort: Sort | null, places: number | null | undefined, count: number): number | null {
  if (!places || places <= 0 || !sort || sort.key !== "total" || places >= count) return null;
  return sort.dir === "desc" ? places : count - places;
}

export const decisionLabel = (d: ScoreRow["decision"]) => (d === "selected" ? "Selected" : d === "not_selected_competitive" ? "Not selected (competitive)" : "Not decided");
