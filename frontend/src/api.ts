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

export interface QueueItem {
  id: string;
  reference: string;
  applicant_name: string | null;
  program_name: string | null;
  status: string;
  submitted_at: string | null;
  attention: Attention;
  open_items: number;
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
  quote: string;
  verified?: boolean;
  method?: string;
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
}

export interface DocumentRow {
  id: string;
  file_name: string;
  declared_type: string;
  detected_type: string | null;
  type_matches: boolean | null;
  needs_verification: boolean;
  verification_notes: { field: string; result: string; label: string }[];
  extracted_text: string | null;
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
}

export interface Detail {
  application: ApplicationRecord;
  documents: DocumentRow[];
  latest_run: Run | null;
  findings: Finding[];
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
  evaluationLatest: () => request<{ evaluation_run: EvaluationRun | null; message?: string }>("/evaluation/latest"),
};
