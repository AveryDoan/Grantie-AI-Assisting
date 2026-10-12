// What counts as decided, for showing a row's state. The sub-step counts (pills, cards, header) come from the backend
// (steps endpoint, step 3 "substeps" and "progress"); nothing in the browser counts them on its own.
import type { Finding } from "./api";

/** A merit criterion is done once the officer has marked it (or set it to Not assessed); every other rule by a review. */
export function isDecided(f: Finding): boolean {
  if (f.section === "merit") return !!f.merit_mark;
  return !!f.latest_review && f.latest_review.action !== "ask_applicant";
}

export const isRule = (f: Finding) => f.section !== "merit";
