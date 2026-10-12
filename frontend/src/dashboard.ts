// Overview helpers: filter keys and labels (the same keys the backend's queue filter takes), section classes and plain wording.
// Counts only. Section colours say WHICH area of work, never how good anything is.
import type { DashStage, DashTile } from "./api";

export const STAGE_SEC: Record<DashStage, string> = {
  documents: "sec-docs", redaction: "sec-redaction", eligibility: "sec-elig", consistency: "sec-consistency",
  ai_use: "sec-ai", merit: "sec-merit", outcome: "sec-outcome", signed_off: "sec-done",
};

export const STAGE_TITLE: Record<DashStage, string> = {
  documents: "Documents", redaction: "Redaction check", eligibility: "Eligibility", consistency: "Consistency",
  ai_use: "AI-use check", merit: "Merit", outcome: "Outcome", signed_off: "Signed off",
};

export const TILES: { key: DashTile; label: string; line: string; icon: string; sec: string }[] = [
  { key: "received", label: "Received", line: "Applications in this round", icon: "list", sec: "sec-outcome" },
  { key: "need_action", label: "Need your action", line: "Waiting on an officer", icon: "user", sec: "sec-elig" },
  { key: "waiting_applicant", label: "Waiting on applicant", line: "A request for more is out", icon: "mail", sec: "sec-docs" },
  { key: "ready_signoff", label: "Ready for sign-off", line: "Every step before Outcome done", icon: "shield", sec: "sec-redaction" },
  { key: "signed_off", label: "Signed off", line: "Record locked", icon: "check", sec: "sec-done" },
  { key: "not_started", label: "Not started", line: "No step opened yet", icon: "clock", sec: "sec-consistency" },
];

// Outcome letters: counts only, never a judgement. Each opens the queue with the same filter the count used.
export const OUTCOME_TILES: { key: "ineligible_to_sign" | "round_ready" | "not_selected_to_release"; label: string; line: string; icon: string; sec: string; href?: string }[] = [
  { key: "ineligible_to_sign", label: "Not eligible, letters to sign off", line: "A rule confirmed Not met by an officer", icon: "file", sec: "sec-outcome" },
  { key: "round_ready", label: "Ready for round decision", line: "Eligible, merit marked", icon: "check", sec: "sec-merit", href: "#/round-decisions" },
  { key: "not_selected_to_release", label: "Not selected, letters to release", line: "After the round decision", icon: "mail", sec: "sec-consistency" },
];

export const FILTER_LABEL: Record<string, string> = {
  received: "Received", need_action: "Need an officer's action", waiting_applicant: "Waiting on applicant",
  ready_signoff: "Ready for sign-off", signed_off: "Signed off", not_started: "Not started",
  "signal:consistency": "Differences to check (Consistency)", "signal:ai_use": "AI-use signals",
  ineligible_to_sign: "Not eligible, letters to sign off", round_ready: "Ready for round decision", not_selected_to_release: "Not selected, letters to release",
  ...Object.fromEntries(Object.entries(STAGE_TITLE).map(([k, t]) => [`stage:${k}`, `Stage: ${t}`])),
};

const REASON: Record<string, string> = {
  not_provided: "not provided", wrong_type: "wrong document type", unreadable: "unreadable or scanned", differs: "details differ from the form",
  asked_again: "officer asked to see it again",
};

export function filterLabel(key: string): string {
  if (key.startsWith("docs:")) {
    const [, slot, reason] = key.split(":");
    const type = { coe: "Confirmation of Enrolment", arrival: "Evidence of arrival date", letter: "Letter of support", bio: "Biography and headshot", other: "Other supporting documents" }[slot] ?? slot;
    return `Documents: ${type}${reason ? `, ${REASON[reason] ?? reason}` : ""}`;
  }
  return FILTER_LABEL[key] ?? key;
}

/** The queue address a tile, card or bar opens. The backend applies the same filter the count used. */
export function queueHref(filter: string, officer: "me" | "all", rulePack?: string): string {
  const q = new URLSearchParams({ filter, ...(officer === "me" ? { officer } : {}), ...(rulePack ? { pack: rulePack } : {}) });
  return `#/queue?${q}`;
}

export function updatedAgo(iso: string, now = Date.now()): string {
  const mins = Math.floor((now - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "Updated just now";
  return `Updated ${mins} ${mins === 1 ? "minute" : "minutes"} ago`;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function reminderText(ref: string, days: number | null): string {
  return [
    "Hello,",
    "",
    `We are still waiting for the information we asked for about your application ${ref}${days != null ? ` (our request was sent ${plural(days, "day")} ago)` : ""}.`,
    "Please reply with the documents or details in the request. If something is unclear, reply to this message and we will help.",
    "",
    "Kind regards,",
    "Grants team",
  ].join("\n");
}
