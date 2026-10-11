// Where each application is in the four steps. Every application is in exactly one stage, so the counts add up to the total.
// Counts only: this is a way to find work, not a ranking.
import type { QueueItem } from "./api";

export type Stage = "1" | "2" | "3" | "4" | "waiting";
export const STAGES: { id: Stage; label: string; sec: string }[] = [
  { id: "1", label: "Documents", sec: "sec-docs" },
  { id: "2", label: "Redaction check", sec: "sec-redaction" },
  { id: "3", label: "Assessment", sec: "sec-elig" },
  { id: "4", label: "Outcome", sec: "sec-outcome" },   // includes applications that are signed off; the Status column tells them apart
];

export function stageOf(item: Pick<QueueItem, "phase" | "current_step">): Stage {
  if (item.phase === "done") return "4";
  if (item.phase === "waiting") return "waiting";
  return String(item.current_step) as Stage;
}

export function stageCounts(items: Pick<QueueItem, "phase" | "current_step">[]): Record<Stage, number> {
  const c: Record<Stage, number> = { "1": 0, "2": 0, "3": 0, "4": 0, waiting: 0 };
  for (const i of items) c[stageOf(i)] += 1;
  return c;
}
