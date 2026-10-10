// Typed client for the FastAPI backend. Every request carries the officer's
// Supabase (or demo) access token. No keys live in the browser.

export type Decision = "Met" | "Not met" | "Needs evidence" | "Unclear";
export type AIStatus = Decision | "Evidence only";

export interface Attention {
  not_yet_assessed: boolean;
  manual_assessment_requested: boolean;
  failed_runs: number;
  documents_needing_verification: number;
  unreviewed_findings: number;
  invalid_findings: number;
  error_findings: number;
  language_flags: number;
  injection_flags: number;
  awaiting_applicant: boolean;
}

// ---- Consistency layer: signals for the officer to check. Never a verdict, score or recommendation.
export interface FlagEvidence {
  kind: "quote" | "field" | "signal" | "link";
  source: string;                 // "application_text", "document:<id>", "form", or "application:<id>" for a link
  label: string;
  quote?: string;                 // as the AI saw it (placeholders included)
  restored?: string | null;       // the applicant's own words, restored for the officer
  verified?: boolean;
  field?: string;
  value?: string;
}

export interface ConsistencyFlag {
  id: string;
  check_id: string;
  check_type: "cross_document" | "timeline" | "document_integrity" | "cross_application" | "narrative";
  type_label: string;
  strength: "strong" | "weak";
  description: string;
  evidence: FlagEvidence[];
  verification: "verified" | "unclear";
  status: "open" | "confirmed" | "dismissed";
  note: string | null;
  reviewed_at: string | null;
}

export interface TraceItem {
  label?: string; kind?: string; start?: string | null; end?: string | null; full_time?: boolean | null;
  topic?: string; first_quote?: string; second_quote?: string; quote?: string; verified: boolean;
  source?: string | null; first_source?: string | null; second_source?: string | null; method?: string;
}

export interface ConsistencyTrace {
  cross_document?: { flags?: number; error?: string };
  document_integrity?: { flags?: number; error?: string };
  timeline?: { input?: { sources: { source: string; label: string; characters: number }[]; notes: string[] }; returned?: TraceItem[]; verified?: number; dropped_unverified?: number; flags?: number; error?: string; skipped?: string };
  narrative?: { input?: { sources: { source: string; label: string; characters: number }[]; notes: string[] }; returned?: TraceItem[]; verified?: number; dropped_unverified?: number; flags?: number; error?: string; skipped?: string };
  cross_application?: { identifiers?: number; fingerprints?: number; linked_applications?: number; skipped?: string };
}

/** One side of a comparison: the value, where it came from, and (when known) where it sits in the original text. */
export interface CompareSide {
  label: string;
  value: string | null;
  source: string;                 // "application_text" or "document:<id>"
  source_label: string | null;
  start: number | null;
  end: number | null;
  has_text: boolean;
}

export type OverviewResult = "Consistent" | "Differs" | "Cannot compare" | "Needs evidence";
export interface OverviewRow {
  id: string;
  check: string;
  compared: string;               // "Name on the form vs name on the CoE"
  result: OverviewResult;
  why: string | null;
  decision: "Not decided" | "Needs follow-up" | "Dismissed" | "Not needed";
  left: CompareSide | null;
  right: CompareSide | null;
  flag_ids: string[];
}

export interface ConsistencyView {
  enabled: boolean;
  unavailable?: boolean;
  flags: ConsistencyFlag[];
  trace: ConsistencyTrace;
  overview?: OverviewRow[];
}

/** Another application that shares something with this one. Hashed references only; never another applicant's details. */
export interface LinkedApplication {
  application_id: string;
  reference: string;
  shared: { what: string; ref: string; strength: "Exact match" | "Similar" }[];
  strength: "Exact match" | "Similar";
  can_open: boolean;
}

export interface MeritMark { mark: number | null; not_assessed: boolean; reason: string | null; updated_at: string | null }

export interface LinkedGroup {
  id: string;
  applications: { id: string; reference: string; applicant_name: string | null }[];
  attributes: { check_id: string; label: string; strength: "strong" | "weak" }[];
  open_flags: number;
}

export interface QueueItem {
  id: string;
  reference: string;
  applicant_name: string | null;
  program_name: string | null;
  status: string;
  submitted_at: string | null;
  attention: Attention;
  open_items: number;
  waiting_since?: string | null;   // when the request for more documents was sent (only while waiting for the applicant)
  flags_to_check: number;   // a count only: shown when at least one open flag is strong; never used to sort
}

export interface Review {
  id: string;
  seq: number;
  officer_id: string;
  action: "confirm" | "override" | "ask_applicant";
  final_status: Decision | null;
  reason: string | null;
  reviewed_at: string;
}

export interface SupportingQuote {
  span?: TextSpan | null;
  quote: string;
  verified?: boolean;
  method?: string;
  source?: string; // "document:<id>" when the quote comes from an uploaded letter
  label?: string;
}

/** Where a passage sits in the ORIGINAL text of one source (offsets into Detail.source_texts[source].text). */
export interface TextSpan { source: string; start: number; end: number; text: string; method?: string }

export interface RuleSource { typed: string | null; document_type: string | null; document_field: string | null }

export interface AiSummary {
  text: string | null;
  linked: boolean;   // at least one of its passages was found in the text by code
  passages: { quote: string | null; verified: boolean; span: TextSpan | null }[];
}

export interface Finding {
  id: string;
  rule_id: string;
  rule_code: string;
  rule_text: string;
  rule_type: string;
  source_clause: string | null;
  ai_status: AIStatus;
  rationale: string | null;
  evidence_quote: string | null;
  evidence_quote_restored: string | null;
  evidence_span?: TextSpan | null;
  rule_sources?: RuleSource[];
  ai_summaries_restored?: AiSummary[];
  quote_verified: boolean;
  supporting_quotes_restored: SupportingQuote[];
  confidence: "high" | "medium" | "low" | null;
  language_flag: boolean;
  needs_applicant_clarification: boolean;
  is_valid: boolean;
  error_flag: boolean;
  error_detail: string | null;
  check_source: "llm" | "code" | "human_only";
  latest_review: Review | null;
  review_history: Review[];
  section: "eligibility" | "documents" | "merit" | null;
  weight: number | null;
  merit_mark?: MeritMark | null;   // the officer's own mark for a merit criterion; null until they mark it
  rule_documents?: RuleDocument[];   // which documents this rule uses, with their status (from config/rule_guidance.yaml)
  verify_note?: string | null;       // what the officer needs to verify
}

export interface RuleDocument { type: string; label: string; file_name: string | null; document_id: string | null; status: string }

export interface EvidenceItem { finding_id: string; rule_code: string; label: string; ask: string; why: string }
export interface EvidenceDraft {
  id: string; message_text: string; status: string; sent_at: string | null;
  to: string; subject: string; not_sent_note: string; items: EvidenceItem[];
}

export interface DocumentRow {
  id: string;
  file_name: string;
  declared_type: string;
  redacted_text?: string | null;
  extraction_status?: "ok" | "no_text" | "unsupported" | null;
  needs_manual_review?: boolean;
  detected_type: string | null;
  type_matches: boolean | null;
  needs_verification: boolean;
  verification_notes: { field: string; result: string; label: string }[];
  extracted_text: string | null;
  extracted_fields?: Record<string, string>;
  is_pdf?: boolean;
  superseded?: boolean;
  attention_level?: "ok" | "check" | "attention" | null;
  attention_reason?: string | null;
}

export interface InjectionFlag {
  pattern: string;
  source: string;
  excerpt: string;
}

export interface Run {
  id: string;
  status: string;
  rule_pack_version: string;
  model_name: string;
  finished_at: string | null;
  injection_flags: InjectionFlag[];
}

export interface QualityChecks {
  quotes_match_application: boolean;
  unmatched_quotes: string[];
  every_reason_cites_confirmed_rule: boolean;
  reading_grade: number;
  reading_grade_target: number;
  reading_grade_ok: boolean;
  no_score_or_ranking_language: boolean;
  includes_review_info: boolean;
  checklist: {
    rule: string;
    cites_rule: boolean;
    quotes_applicant: boolean;
    explains_link: boolean;
    says_what_would_change: boolean;
    includes_review_info: boolean;
  }[];
  all_passed: boolean;
}

export interface Letter {
  id: string;
  kind?: "decline" | "next_steps";
  version: number;
  body_text: string;
  status: "draft" | "edited" | "approved";
  reading_grade: number | null;
  quality_checks: QualityChecks;
  approved_at: string | null;
  created_at: string;
}

export interface ApplicationRecord {
  id: string;
  reference: string;
  applicant_name: string | null;
  program_name: string | null;
  status: string;
  submitted_at: string | null;
  manual_assessment_requested: boolean;
  application_text: { fields?: Record<string, string>; answers?: Record<string, string> };
  redacted_text?: string | null;
  ai_status?: "not_redacted" | "ready" | "blocked_redaction_leak" | "blocked_low_confidence";
  location_class?: string | null;
}

export interface Fact {
  id: string;
  fact_key: string;
  fact_value: string;
  source_quote: string | null;
  quote_verified: boolean;
  source: string | null;
}

export interface RawQuote {
  quote: string;
  verified?: boolean;
  method?: string;
  source?: string;
  label?: string;
}

export interface Detail {
  application: ApplicationRecord;
  documents: DocumentRow[];
  source_texts?: Record<string, { label: string; text: string }>;
  linked_applications?: LinkedApplication[];
  latest_run: Run | null;
  consistency: ConsistencyView;
  facts: Fact[];
  findings: (Finding & { supporting_quotes?: RawQuote[] })[];
  letters: Letter[];
  sign_off: { signed_at: string; officer_id: string } | null;
  attention: Attention;
}

export interface AuditRow {
  id: string;
  occurred_at: string;
  actor_name: string | null;
  actor_role: string;
  action: string;
  application_reference: string | null;
  rule_code: string | null;
  rule_text: string | null;
  ai_suggestion: string | null;
  officer_decision: string | null;
  overridden: boolean;
  reason: string | null;
  rule_pack_version: string | null;
}

export interface TwinGroup {
  family_id: string;
  rule_code: string;
  consistent: boolean;
  members: { case_code: string; style: string | null; predicted: string | null }[];
}

export interface EvaluationRun {
  id: string;
  model_name: string;
  prompt_version: string;
  started_at: string;
  finished_at: string | null;
  report_markdown: string | null;
  summary: {
    provider?: string;
    cases: number;
    rule_checks: number;
    accuracy: number | null;
    accuracy_by_language_style: Record<string, number | null>;
    quote_validity_rate: number | null;
    quotes_checked: number;
    twin_consistency_rate: number | null;
    twin_groups?: TwinGroup[];
    injection_tests: { case_code: string; flagged: boolean; obeyed: boolean; patterns: string[] }[];
    failures: number;
    invalid_findings?: number;
  };
}

export interface RedactionReport {
  application_id: string;
  ai_status: string;
  location_class: string | null;
  run: {
    id: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    counts: Record<string, number>;
    leak_scan: Record<string, number>;
    detector_version: string;
    config_hash: string;
  } | null;
  tokens: { token: string; entity_type: string; occurrences: number; sources: string[] }[];
  documents: { document_id: string; declared_type: string; extraction_status: string | null; needs_manual_review: boolean; included_in_ai_input: boolean }[];
}

export interface OriginalView {
  application_id: string;
  application_text: string;
  matches_stored_original: boolean;
  documents: { document_id: string; text: string }[];
}

export interface Program { id: string; name: string; description: string | null }

export interface UploadResult {
  id: string;
  file_name: string;
  declared_type: string;
  kind: string;
  extraction_status: "ok" | "no_text" | "unsupported";
  needs_manual_review: boolean;
  pages: number;
  looks_like: string | null;
}

export interface DraftCheck {
  missing_fields: { field: string; label: string; rules: string[] }[];
  missing_documents: { document_type: string; label: string; needed: number; provided: number }[];
  wrong_document_type: { file_name: string; declared_as: string; looks_like: string; message: string }[];
  items_to_check: number;
  note: string;
}

export interface MyApplication { id: string; status: string; submitted_at: string | null; reference: string }

export interface ApplicantDetail {
  application: { id: string; status: string; submitted_at: string | null; manual_assessment_requested: boolean;
    application_text: { fields?: Record<string, string>; answers?: Record<string, string> } };
  documents: { id: string; file_name: string; declared_type: string; uploaded_at: string }[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

let token: string | null = null;
let onUnauthorised: (() => void) | null = null;

export function setToken(t: string | null) {
  token = t;
}

export function setUnauthorisedHandler(fn: () => void) {
  onUnauthorised = fn;
}

export type ReferenceListSummary = {
  name: string; description: string | null; source: string | null; edition: string | null; count: number; loaded: boolean;
  tiers: Record<string, number>; updated_at: string | null; used_by: string[];
};
export type ReferenceListEntry = { code: string; name: string; skill_level: number | null; tier: string };
export type ReferenceListDetail = ReferenceListSummary & { entries: ReferenceListEntry[]; entries_truncated: boolean };
export type ReferenceListPreview = {
  count: number; tiers: Record<string, number>; warnings: string[]; sample: ReferenceListEntry[]; current_count: number;
  added: string[]; removed: string[]; added_count: number; removed_count: number;
};

// ---- Guided steps: Documents, Redaction check, Assessment, Outcome
export type StepStatus = "not_started" | "in_progress" | "done" | "waiting";
export interface StepInfo {
  step: 1 | 2 | 3 | 4; key: string; title: string; status: StepStatus; label: string; unlocked: boolean;
  missing: string[]; notice: string | null; pending_rules?: string[];
}
export interface StepsState { steps: StepInfo[]; current: number; legacy: boolean }

export interface DocSlot {
  slot: string; label: string; required: boolean; document_id: string | null; file_name: string | null;
  decision: "confirmed" | "wrong_slot" | "request_again" | "not_needed" | null; reason: string | null;
  issue: { kind: "missing" | "wrong_slot" | "unreadable" | "differs" | "other"; reason: string } | null;
  replaces: { old: { file_name: string; text: string | null; fields: Record<string, string> }; new: { file_name: string; text: string | null; fields: Record<string, string> } } | null;
}
export interface DocRequest { id: string; status: "draft" | "sent"; message_text: string; items: { slot: string; label: string; kind: string; reason: string; ask: string; why: string }[];
  created_at: string | null; sent_at: string | null; approved_at: string | null; resubmitted_at: string | null }
export interface DocumentsStep {
  slots: DocSlot[]; suggested_items: { slot: string; label: string; kind: string; reason: string }[]; requests: DocRequest[]; missing: string[];
}

export interface RedactionGroup { type: string; count: number; how: string[]; where: string[] }
export interface RedactionCheck {
  run: { id: string; status: string; started_at: string; finished_at: string | null; counts: Record<string, number>; detector_version: string } | null;
  ai_status: string; leak_scan: { passed: boolean; found: number; checks: Record<string, number> }; groups: RedactionGroup[];
  blockers: string[]; edits: { added: number; unmasked: number }; texts: Record<string, { label: string; redacted: string }>;
  documents: { id: string; file_name: string; declared_type: string | null; is_pdf: boolean; redacted_spans: number }[];
}
export interface RedactionItem { id: string; type: string; token: string; source: string; where: string; before: string; after: string }

export interface OutcomeUnmet { rule_code: string; rule_text: string; final_status: string; reason: string; quote: string | null; what_would_change: string }
export interface Outcome {
  application_id: string; statement: string; signed_off: boolean;
  result: "not_assessed" | "undecided" | "decline" | "needs_information" | "eligible";
  unmet: OutcomeUnmet[]; needs_information: OutcomeUnmet[]; letter: Letter | null; letter_kind?: "decline" | "next_steps" | null;
  summary: { marks: { rule_code: string; criterion: string; mark: number | null; not_assessed: boolean; reason: string | null }[]; signals_kept: { label: string; note: string | null }[]; note: string } | null;
  record: { outcome: string; officer: string; signed_at: string; version: number; letter_version: number | null; letter_status: string | null;
    decisions: { rule_code: string; rule_text: string; decision: string; ai_status: string; reason: string | null; overridden?: boolean }[];
    reopened: { version: number; reopened_at: string; reason: string }[] } | null;
  pending_rules?: string[];
}

// ---- The original PDF: signed link, evidence highlights, redaction blur
export interface PdfRect { page: number; x0: number; y0: number; x1: number; y1: number }
export interface PdfViewerInfo { document_id: string; file_name: string; declared_type: string | null; pages: { page: number; width: number; height: number }[]; url: string; expires_in: number }
export interface PdfLocateItem { id: string; start?: number | null; end?: number | null; text?: string | null }
export interface PdfLocation { status: "exact" | "approximate" | "not_found"; rects: PdfRect[]; method: string }
export interface PdfBlurBox { id: string; token: string; type: string; rects: PdfRect[]; found: boolean }
export interface PdfBlur { boxes: PdfBlurBox[]; counts: Record<string, number>; redacted: boolean }

async function request<T>(path: string, init: { method?: string; body?: unknown; raw?: boolean } = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init.method ?? "GET",
    headers: {
      ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 401) onUnauthorised?.();
  if (!res.ok) {
    let payload: { error?: string; message?: string; detail?: string; details?: Record<string, unknown> } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    const message = payload.message ?? payload.detail ?? `Request failed (${res.status})`;
    throw new ApiError(res.status, payload.error ?? "error", typeof message === "string" ? message : JSON.stringify(message), payload.details);
  }
  return (init.raw ? res.text() : res.json()) as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; mode: "demo" | "supabase" }>("/health"),
  demoLogin: (role: string) =>
    request<{ access_token: string; display_name: string }>("/demo/login", { method: "POST", body: { role } }),
  me: () => request<{ user_id: string; role: string; display_name: string | null }>("/me"),
  queue: () => request<QueueItem[]>("/applications"),
  detail: (id: string) => request<Detail>(`/applications/${id}`),
  assess: (id: string) => request<{ id: string; reused: boolean }>(`/applications/${id}/assess`, { method: "POST", body: {} }),
  review: (findingId: string, body: { action: Review["action"]; final_status?: Decision; reason?: string }) =>
    request<Review>(`/findings/${findingId}/review`, { method: "POST", body }),
  clarify: (id: string, body: { finding_id?: string; message_text?: string; send: boolean }) =>
    request<{ id: string; status: string }>(`/applications/${id}/clarification`, { method: "POST", body }),
  evidenceItems: (id: string) => request<EvidenceItem[]>(`/applications/${id}/evidence-request/items`),
  draftEvidenceRequest: (id: string, finding_ids?: string[]) =>
    request<EvidenceDraft>(`/applications/${id}/evidence-request`, { method: "POST", body: { finding_ids: finding_ids ?? null } }),
  signoffStatement: () => request<{ statement: string }>("/signoff-statement"),
  signoff: (id: string) =>
    request<{ id: string }>(`/applications/${id}/signoff`, { method: "POST", body: { statement_acknowledged: true } }),
  generateLetter: (id: string) => request<Letter>(`/applications/${id}/letter`, { method: "POST" }),
  patchLetter: (letterId: string, body: { body_text?: string; approve?: boolean }) =>
    request<Letter>(`/letters/${letterId}`, { method: "PATCH", body }),
  audit: (params: Record<string, string> = {}) =>
    request<AuditRow[]>(`/audit-log?${new URLSearchParams(params)}`),
  auditCsv: (params: Record<string, string> = {}) =>
    request<string>(`/audit-log?${new URLSearchParams({ ...params, format: "csv" })}`, { raw: true }),
  setMeritMark: (applicationId: string, ruleCode: string, body: { mark: number | null; not_assessed: boolean; reason?: string }) =>
    request<MeritMark>(`/applications/${applicationId}/merit-marks/${ruleCode}`, { method: "PUT", body }),
  documentViewer: (docId: string) => request<PdfViewerInfo>(`/documents/${docId}/viewer`),
  locateItems: (docId: string, items: PdfLocateItem[]) => request<Record<string, PdfLocation>>(`/documents/${docId}/locate`, { method: "POST", body: { items } }),
  documentBlur: (docId: string) => request<PdfBlur>(`/documents/${docId}/blur`),
  downloadPdf: async (path: string, fileName: string) => {
    const res = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new ApiError(res.status, "error", "The file could not be exported");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = fileName; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },
  steps: (id: string) => request<StepsState>(`/applications/${id}/steps`),
  documentsStep: (id: string) => request<DocumentsStep>(`/applications/${id}/documents-step`),
  documentDecision: (id: string, body: { slot: string; decision: string; reason?: string }) =>
    request<unknown>(`/applications/${id}/documents-step/decision`, { method: "POST", body }),
  draftRequest: (id: string, items: { slot: string; kind: string; reason?: string; note?: string }[]) =>
    request<DocRequest>(`/applications/${id}/documents-step/requests`, { method: "POST", body: { items } }),
  editRequest: (requestId: string, message_text: string) => request<DocRequest>(`/document-requests/${requestId}`, { method: "PATCH", body: { message_text } }),
  sendRequest: (requestId: string) => request<DocRequest>(`/document-requests/${requestId}/send`, { method: "POST" }),
  completeDocuments: (id: string) => request<StepsState>(`/applications/${id}/steps/documents/complete`, { method: "POST" }),
  demoApplicantReply: (id: string) => request<unknown>(`/demo/applicant-reply/${id}`, { method: "POST" }),
  redactionCheck: (id: string) => request<RedactionCheck>(`/applications/${id}/redaction-check`),
  redactionItems: (id: string, type: string) => request<RedactionItem[]>(`/applications/${id}/redaction-check/items?${new URLSearchParams({ type })}`),
  revealItem: (id: string, itemId: string) => request<{ item_id: string; original: string }>(`/applications/${id}/redaction-check/reveal`, { method: "POST", body: { item_id: itemId } }),
  addMissed: (id: string, body: { source: string; text: string; kind: string }) => request<RedactionCheck>(`/applications/${id}/redaction-check/add`, { method: "POST", body }),
  unmaskItem: (id: string, itemId: string, reason: string) => request<RedactionCheck>(`/applications/${id}/redaction-check/unmask`, { method: "POST", body: { item_id: itemId, reason } }),
  approveRedaction: (id: string) => request<{ steps: StepsState }>(`/applications/${id}/redaction-check/approve`, { method: "POST" }),
  outcome: (id: string) => request<Outcome>(`/applications/${id}/outcome`),
  nextStepsLetter: (id: string) => request<Letter>(`/applications/${id}/letter/next-steps`, { method: "POST" }),
  reopen: (id: string, reason: string) => request<{ status: string }>(`/applications/${id}/reopen`, { method: "POST", body: { reason } }),
  reviewFlag: (flagId: string, body: { action: "confirm" | "dismiss"; note?: string }) =>
    request<ConsistencyFlag>(`/consistency-flags/${flagId}/review`, { method: "POST", body }),
  referenceLists: () => request<ReferenceListSummary[]>("/reference-lists"),
  referenceList: (name: string, q = "") => request<ReferenceListDetail>(`/reference-lists/${name}?${new URLSearchParams(q ? { q } : {})}`),
  previewReferenceList: (name: string, text: string) =>
    request<ReferenceListPreview>(`/reference-lists/${name}/preview`, { method: "POST", body: { text } }),
  saveReferenceList: (name: string, body: { text: string; edition?: string; source?: string }) =>
    request<ReferenceListSummary & { warnings: string[] }>(`/reference-lists/${name}`, { method: "PUT", body }),
  linkedGroups: () => request<LinkedGroup[]>("/pool/linked-applications"),
  redact: (id: string, force = false) =>
    request<{ run_id: string; reused: boolean; report: Record<string, unknown> }>(`/applications/${id}/redact`, { method: "POST", body: { force } }),
  redactionReport: (id: string) => request<RedactionReport>(`/applications/${id}/redaction-report`),
  originalView: (id: string) => request<OriginalView>(`/applications/${id}/original-view`),
  // applicant
  programs: () => request<Program[]>("/programs"),
  myApplications: () => request<MyApplication[]>("/me/applications"),
  applicantDetail: (id: string) => request<ApplicantDetail>(`/applications/${id}`),
  createDraft: (body: { grant_program_id?: string; fields: Record<string, string>; answers: Record<string, string> }) =>
    request<{ id: string; status: string }>("/me/applications", { method: "POST", body }),
  saveDraft: (id: string, body: { fields: Record<string, string>; answers: Record<string, string> }) =>
    request<{ id: string; status: string }>(`/me/applications/${id}`, { method: "PUT", body }),
  uploadDocument: (id: string, body: { file_name: string; declared_type: string; content_base64: string }) =>
    request<UploadResult>(`/me/applications/${id}/documents`, { method: "POST", body }),
  removeDocument: (id: string, docId: string) =>
    request<{ removed: string }>(`/me/applications/${id}/documents/${docId}`, { method: "DELETE" }),
  checkDraft: (id: string) => request<DraftCheck>(`/me/applications/${id}/check`),
  submitDraft: (id: string, manual_assessment: boolean) =>
    request<{ id: string; status: string; reference: string; submitted_at: string }>(`/me/applications/${id}/submit`, { method: "POST", body: { manual_assessment } }),
  evaluationLatest: () => request<{ evaluation_run: EvaluationRun | null; message?: string }>("/evaluation/latest"),
};
