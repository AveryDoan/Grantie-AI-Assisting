// What the review screen shows, worked out from the data the API already returns. Nothing here calls the API,
// scores anything, or changes a rule result: it only decides how rows are grouped, labelled and ordered.
import type { AIStatus, ConsistencyFlag, Detail, DocumentRow, Finding } from "../../api";
import { effectiveStatus, humanise, isDecided } from "../../ui";

export const DOC_LABEL: Record<string, string> = {
  coe: "Confirmation of Enrolment", visa: "Visa grant notice", travel_document: "Passport / travel document",
  passport: "Passport / travel document", offer_letter: "Letter of offer", travel_booking: "Travel booking",
  flight_screenshot: "Flight screenshot", referee_letter: "Letter of support", headshot: "Headshot",
  certified_translation: "Certified translation", other: "Other document",
};
export const SECTION_LABEL: Record<string, string> = { eligibility: "Eligibility", documents: "Documents", merit: "Merit criteria" };

// ---------------------------------------------------------------- checked-by tags

export type CheckedBy = "code" | "ai" | "records" | "officer";
export const CHECKED_BY_LABEL: Record<CheckedBy, string> = {
  code: "Calculated by code", ai: "AI suggestion", records: "Records check", officer: "Officer only",
};
export function checkedBy(f: Finding): CheckedBy {
  if (f.check_source === "human_only") return "officer";
  if (f.check_source === "llm") return "ai";
  return f.rule_type === "cross_application" ? "records" : "code";
}

// ---------------------------------------------------------------- findings

export type DecisionLabel = "Not decided" | "Confirmed" | "Overridden" | "Asked applicant";
export function decisionOf(f: Finding): DecisionLabel {
  const r = f.latest_review;
  if (!r) return "Not decided";
  if (r.action === "ask_applicant") return "Asked applicant";
  return r.action === "override" || (r.final_status && r.final_status !== f.ai_status) ? "Overridden" : "Confirmed";
}
/** Judgement rules are decided by recording the officer's own status, so "Recorded" is the plain word for those. */
export function decisionText(f: Finding): string {
  const d = decisionOf(f);
  return f.ai_status === "Evidence only" && d !== "Not decided" && d !== "Asked applicant" ? "Recorded" : d;
}

const PRIORITY: Record<string, number> = { Unclear: 0, "Needs evidence": 1, "Not met": 2, "Evidence only": 3, Met: 4 };
/** Attention first: undecided before decided; invalid or errored findings, then Unclear, Needs evidence, Not met, Met. */
export function attentionKey(f: Finding): number {
  const base = isDecided(f) ? 100 : 0;
  const invalid = !f.is_valid || f.error_flag ? -10 : 0;
  return base + invalid + (PRIORITY[f.ai_status] ?? 5);
}
export const byAttention = (a: Finding, b: Finding) => attentionKey(a) - attentionKey(b);

export const STATUS_ORDER: AIStatus[] = ["Met", "Not met", "Unclear", "Needs evidence", "Evidence only"];

/** The one line the table shows. Rule text with its trailing weight sentence removed. */
export function plainRule(f: Finding): string {
  return f.rule_text.replace(/\s*Weight on the form: \d+%\.?/i, "").replace(/\s*\(Officer judgement\.\)/i, "").trim();
}

// ---------------------------------------------------------------- documents

export interface Slot {
  key: string;
  label: string;
  optional?: boolean;
  doc: DocumentRow | null;
  /** Documents-section rules that read this slot. Every D rule belongs to exactly one slot, so none can be missed. */
  rules: string[];
  /** The form has typed text where a file was expected. */
  commentInstead?: boolean;
  extraDocs?: DocumentRow[];
}
export type SlotCheck = "Right document" | "Wrong document type" | "Unreadable" | "Missing" | "Comment instead of a file" | "Not checked" | "None uploaded";
export type DetailsCheck = "Matches" | "Needs verification" | "Not checked";

const ARRIVAL = new Set(["travel_booking", "flight_screenshot", "booking", "itinerary", "travel booking"]);
const COE = new Set(["coe", "confirmation of enrolment", "enrolment"]);
const LETTER = new Set(["referee_letter", "referee letter", "reference letter"]);
const BIO = new Set(["headshot", "photo", "biography"]);

export function buildSlots(detail: Detail): Slot[] {
  const docs = detail.documents;
  const type = (d: DocumentRow) => d.declared_type.trim().toLowerCase();
  const coe = docs.filter((d) => COE.has(type(d)));
  const arrival = docs.filter((d) => ARRIVAL.has(type(d)));
  const letters = docs.filter((d) => LETTER.has(type(d)));
  const bio = docs.filter((d) => BIO.has(type(d)));
  const used = new Set([...coe, ...arrival, ...letters, ...bio].map((d) => d.id));
  const other = docs.filter((d) => !used.has(d.id));
  const biography = (detail.application.application_text.fields ?? {}).biography || (detail.application.application_text.answers ?? {}).biography;
  return [
    { key: "coe", label: "Confirmation of Enrolment", doc: coe[0] ?? null, rules: ["D1"], extraDocs: coe.slice(1) },
    { key: "arrival", label: "Evidence of arrival date", doc: arrival[0] ?? null, rules: ["D2"], extraDocs: arrival.slice(1) },
    { key: "letter1", label: "Letter of Support #1", doc: letters[0] ?? null, rules: ["D3", "D4", "D5"] },
    { key: "letter2", label: "Letter of Support #2", doc: letters[1] ?? null, rules: ["D3", "D4", "D5"], extraDocs: letters.slice(2) },
    { key: "bio", label: "Biography and headshot", doc: bio[0] ?? null, rules: ["D6"], commentInstead: !bio.length && Boolean(biography), extraDocs: bio.slice(1) },
    { key: "other", label: "Other supporting documents (optional)", optional: true, doc: other[0] ?? null, rules: ["D7"], extraDocs: other.slice(1) },
  ];
}

export function slotCheck(s: Slot): SlotCheck {
  if (!s.doc) return s.commentInstead ? "Comment instead of a file" : s.optional ? "None uploaded" : "Missing";
  const d = s.doc;
  if (s.key !== "bio" && (d.extraction_status === "no_text" || d.extraction_status === "unsupported")) return "Unreadable";
  if (s.key === "bio" && d.extraction_status === "unsupported") return "Unreadable";
  if (d.type_matches === null) return "Not checked";
  return d.type_matches ? "Right document" : "Wrong document type";
}
export function detailsCheck(s: Slot): DetailsCheck {
  if (!s.doc || s.doc.type_matches === null) return "Not checked";
  return s.doc.needs_verification ? "Needs verification" : "Matches";
}
/** Chip for the row, from the checks above. "Not checked" is Unclear, never a quiet Met. */
export function slotStatus(s: Slot): AIStatus {
  const c = slotCheck(s);
  if (c === "None uploaded") return "Met";
  if (c === "Missing" || c === "Comment instead of a file") return "Needs evidence";
  if (c !== "Right document") return "Unclear";
  return detailsCheck(s) === "Matches" ? "Met" : "Unclear";
}
// ---------------------------------------------------------------- need (what the Documents table sorts by)

export type Need = "attention" | "check" | "ok";
export const NEED_LABEL: Record<Need, string> = { attention: "Needs attention", check: "Check", ok: "OK" };
const NEED_RANK: Record<Need, number> = { attention: 0, check: 1, ok: 2 };

/** One level and one plain reason per slot. The reasons for uploaded files are worked out and stored by code at assessment. */
export function slotNeed(s: Slot): { level: Need; reason: string | null } {
  const name = s.label.replace(/ \(optional\)$/, "");
  const d = s.doc;
  if (!d) {
    if (s.commentInstead) return { level: "check", reason: "A typed biography was given, but no headshot file was uploaded" };
    if (s.optional) return { level: "ok", reason: null };
    return { level: "attention", reason: s.key.startsWith("letter") ? `${name} was not uploaded` : `No ${name} was uploaded` };
  }
  if (d.attention_level) return { level: d.attention_level, reason: d.attention_reason ?? null };
  // Older runs have no stored reason: say what the checks found, in the same plain words.
  const check = slotCheck(s);
  if (check === "Wrong document type") return { level: "attention", reason: "This file does not look like the right kind of document" };
  if (check === "Unreadable") return { level: "check", reason: "No readable text in this file. A person needs to read it" };
  if (d.needs_verification) return { level: "attention", reason: "Details differ from the form" };
  return { level: check === "Not checked" ? "check" : "ok", reason: check === "Not checked" ? "Not checked yet" : null };
}

export function slotFindings(s: Slot, findings: Finding[]): Finding[] {
  return s.rules.map((code) => findings.find((f) => f.rule_code === code)).filter((f): f is Finding => Boolean(f));
}
export function slotDecision(s: Slot, findings: Finding[]): DecisionLabel {
  const fs = slotFindings(s, findings);
  if (!fs.length || fs.some((f) => decisionOf(f) === "Not decided")) return "Not decided";
  return fs.some((f) => decisionOf(f) === "Overridden") ? "Overridden" : fs.every((f) => decisionOf(f) === "Asked applicant") ? "Asked applicant" : "Confirmed";
}
/** By need first (Needs attention, Check, OK), then undecided before decided. */
export function slotSort(a: Slot, b: Slot, findings: Finding[]): number {
  const k = (s: Slot) => NEED_RANK[slotNeed(s).level] * 10 + (slotDecision(s, findings) === "Not decided" ? 0 : 1);
  return k(a) - k(b);
}

// ---------------------------------------------------------------- story flags

export const FLAG_LABEL: Record<string, string> = {
  "cross_application.identical_text": "Same wording as another application",
  "cross_application.reused_wording": "Similar wording to another application",
  "cross_application.shared_contact_email": "Shares a contact email address",
  "cross_application.shared_contact_phone": "Shares a contact phone number",
  "cross_application.shared_contact_address": "Shares an address",
  "cross_application.shared_referee_name": "Same referee as another application",
  "cross_application.shared_referee_email": "Shares a referee email address",
  "cross_application.shared_referee_phone": "Shares a referee phone number",
  "cross_application.shared_referee_email_domain": "Shares a referee email domain",
  "cross_document.arrival_vs_start": "Arrival date and course start do not fit",
  "cross_document.coe_length": "Course length differs between documents",
  "cross_document.course": "Course differs between documents",
  "cross_document.known_for_vs_timeline": "“Known for” time does not fit the timeline",
  "cross_document.letter_subject": "Letter is about a different person",
  "cross_document.provider": "Provider differs between documents",
  "cross_document.referee_date_future": "Letter is dated in the future",
  "cross_document.referee_date_old": "Letter is dated long ago",
  "cross_document.referee_date_window": "Letter date is outside the allowed period",
  "document_integrity.author_is_applicant": "File author matches the applicant",
  "document_integrity.created_after_dated": "File created after the date it shows",
  "document_integrity.created_before_dated": "File created long before the date it shows",
  "document_integrity.editing_software": "Saved with editing software",
  "document_integrity.modified_after_created": "File changed after it was created",
  "narrative.conflicting_statements": "Two statements disagree",
  "narrative.unverified": "Possible disagreement, not verified",
  "timeline.dates_backwards": "Dates run backwards",
  "timeline.overlapping_full_time": "Full-time activities overlap",
  "timeline.role_before_age": "Role starts before a plausible age",
  "timeline.visa_before_coe": "Visa dated before the CoE",
};
export const TYPE_ORDER: ConsistencyFlag["check_type"][] = ["cross_document", "timeline", "document_integrity", "cross_application", "narrative"];
export const TYPE_CHIP: Record<ConsistencyFlag["check_type"], string> = {
  cross_document: "Across documents", timeline: "Timeline", document_integrity: "Document signals (weak)",
  cross_application: "Across applications", narrative: "Statements that conflict",
};
export const flagLabel = (f: ConsistencyFlag) => FLAG_LABEL[f.check_id] ?? humanise(f.check_id.split(".").pop() ?? f.check_id);

export type FlagStatusLabel = "To check" | "Needs follow-up" | "Dismissed" | "Not verified";
export function flagStatus(f: ConsistencyFlag): FlagStatusLabel {
  if (f.verification === "unclear") return "Not verified";
  return f.status === "open" ? "To check" : f.status === "confirmed" ? "Needs follow-up" : "Dismissed";
}
/** "APP-61214F" from a link's label, when the flag points at another application. */
export function linkedRef(f: ConsistencyFlag): string | null {
  for (const e of f.evidence) {
    if (e.kind !== "link") continue;
    const m = e.label.match(/APP-[A-Z0-9]+/i);
    if (m) return m[0].toUpperCase();
  }
  return null;
}

export interface StoryRow {
  id: string;
  type: ConsistencyFlag["check_type"];
  check: string;
  text: string;
  linked: string | null;
  strength: "Strong" | "Weak";
  status: FlagStatusLabel;
  flags: ConsistencyFlag[];
}

/** Flags that point at the same linked application become one row ("3 items" expands them). */
export function buildStoryRows(flags: ConsistencyFlag[]): StoryRow[] {
  const rows: StoryRow[] = [];
  const grouped = new Map<string, ConsistencyFlag[]>();
  for (const f of flags) {
    const ref = f.check_type === "cross_application" ? linkedRef(f) : null;
    if (!ref) { rows.push(storyRowOf(f)); continue; }
    const key = ref;
    grouped.set(key, [...(grouped.get(key) ?? []), f]);
  }
  for (const [ref, fs] of grouped) {
    if (fs.length === 1) { rows.push(storyRowOf(fs[0])); continue; }
    const open = fs.some((f) => flagStatus(f) === "To check");
    const statuses = [...new Set(fs.map(flagStatus))];
    const kinds = [...new Set(fs.map(flagLabel))];
    rows.push({
      id: `group:${ref}`, type: "cross_application", check: kinds.length === 1 ? kinds[0] : `Several matches with ${ref}`,
      text: kinds.length === 1 ? `${fs.length} matches with ${ref}: ${kinds[0].toLowerCase()}.` : `${kinds.length} kinds of match with ${ref}: ${kinds.map((k) => k.toLowerCase()).join("; ")}.`,
      linked: ref, strength: fs.some((f) => f.strength === "strong") ? "Strong" : "Weak",
      status: open ? "To check" : statuses.length === 1 ? statuses[0] : "Needs follow-up", flags: fs,
    });
  }
  const rank = (r: StoryRow) => (r.status === "To check" ? 0 : r.status === "Not verified" ? 1 : r.status === "Needs follow-up" ? 2 : 3) * 10 + (r.strength === "Strong" ? 0 : 1);
  return rows.sort((a, b) => rank(a) - rank(b) || TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type));
}
export function storyRowOf(f: ConsistencyFlag): StoryRow {
  return { id: f.id, type: f.check_type, check: flagLabel(f), text: f.description, linked: linkedRef(f), strength: f.strength === "strong" ? "Strong" : "Weak", status: flagStatus(f), flags: [f] };
}

export const openFlagCount = (flags: ConsistencyFlag[]) => flags.filter((f) => f.verification === "verified" && f.status === "open").length;
export { effectiveStatus, isDecided };
