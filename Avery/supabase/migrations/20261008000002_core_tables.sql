-- =============================================================================
-- 0002 Core tables.
--
-- SYNTHETIC DATA ONLY. No real applicant data may be loaded into this schema
-- until a privacy impact assessment has been completed.
--
-- Status vocabularies are text + CHECK constraints (easier to evolve than
-- enum types). Business rules that span tables live in 0003 (triggers).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Organisations and user profiles
-- ---------------------------------------------------------------------------

-- Officers are scoped to an organisation; grant programs belong to one.
create table public.organisations (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.profiles (
  id               uuid primary key references auth.users (id) on delete cascade,
  role             text not null default 'applicant'
                     check (role in ('officer', 'applicant', 'admin')),
  organisation_id  uuid references public.organisations (id),
  display_name     text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint officer_has_organisation
    check (role <> 'officer' or organisation_id is not null)
);
comment on table public.profiles is
  'One row per auth user. Role is assigned by an admin; it is never taken from signup metadata.';

-- ---------------------------------------------------------------------------
-- Grant programs, rule packs, rules
-- ---------------------------------------------------------------------------

create table public.grant_programs (
  id               uuid primary key default gen_random_uuid(),
  organisation_id  uuid not null references public.organisations (id),
  name             text not null,
  description      text,
  guidelines_url   text,
  active           boolean not null default true,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table public.rule_packs (
  id                uuid primary key default gen_random_uuid(),
  grant_program_id  uuid not null references public.grant_programs (id),
  version           text not null,
  status            text not null default 'draft'
                      check (status in ('draft', 'approved', 'retired')),
  approved_by       uuid references auth.users (id),
  approved_at       timestamptz,
  source_notes      text,
  -- review process text, contact, deadline wording used in letters
  letter_config     jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (grant_program_id, version),
  constraint approval_recorded
    check (status = 'draft' or (approved_by is not null and approved_at is not null))
);

create table public.rules (
  id              uuid primary key default gen_random_uuid(),
  rule_pack_id    uuid not null references public.rule_packs (id) on delete cascade,
  rule_code       text not null,
  rule_text       text not null,
  source_clause   text,
  source_url      text,
  rule_type       text not null
                    check (rule_type in ('factual', 'document_based', 'cross_application', 'judgement')),
  check_method    text not null check (check_method in ('llm', 'code', 'human_only')),
  params          jsonb not null default '{}'::jsonb,
  display_order   integer not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (rule_pack_id, rule_code),
  -- Judgement can never be decided by code.
  constraint judgement_not_code check (rule_type <> 'judgement' or check_method <> 'code')
);

-- ---------------------------------------------------------------------------
-- Applicants and applications
-- ---------------------------------------------------------------------------

create table public.applicants (
  id                 uuid primary key default gen_random_uuid(),
  -- Login that owns this applicant record (null for seed-only records).
  user_id            uuid unique references auth.users (id) on delete set null,
  display_name       text not null,
  organisation_name  text,
  email              text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
comment on table public.applicants is
  'SYNTHETIC DATA ONLY. Every row must be fictional until a privacy impact assessment is complete.';

create table public.applications (
  id                           uuid primary key default gen_random_uuid(),
  grant_program_id             uuid not null references public.grant_programs (id),
  rule_pack_id                 uuid not null references public.rule_packs (id),  -- the version used
  applicant_id                 uuid not null references public.applicants (id),
  application_text             jsonb not null default '{}'::jsonb,  -- form fields + free text
  redacted_text                text,     -- exactly what the AI saw
  status                       text not null default 'draft'
                                 check (status in ('draft', 'submitted', 'in_review',
                                                   'awaiting_applicant', 'signed_off')),
  -- Evaluation sets only; never used in assessment.
  language_style_tag           text check (language_style_tag in ('polished', 'plain', 'second_language')),
  manual_assessment_requested  boolean not null default false,
  submitted_at                 timestamptz,
  created_at                   timestamptz not null default now(),
  updated_at                   timestamptz not null default now(),
  constraint submitted_has_timestamp check (status = 'draft' or submitted_at is not null)
);
comment on table public.applications is 'SYNTHETIC DATA ONLY.';
comment on column public.applications.language_style_tag is
  'Evaluation sets only. Must never be sent to the LLM or used in any decision.';

-- Token -> original value mapping produced by redaction. Staff-only so the
-- officer sees the full record while the LLM only ever sees redacted_text.
create table public.redaction_maps (
  application_id  uuid primary key references public.applications (id) on delete cascade,
  mapping         jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table public.documents (
  id                  uuid primary key default gen_random_uuid(),
  application_id      uuid not null references public.applications (id) on delete cascade,
  storage_path        text not null,
  file_name           text not null,
  declared_type       text not null,   -- what the applicant says it is
  detected_type       text check (detected_type in ('coe', 'visa', 'travel_document', 'other')),
  type_matches        boolean,
  extracted_fields    jsonb not null default '{}'::jsonb,
  extracted_text      text,
  -- Typed-vs-document mismatches. Always "needs verification", never fraud.
  needs_verification  boolean not null default false,
  verification_notes  jsonb not null default '[]'::jsonb,
  is_sample           boolean not null default true,
  uploaded_at         timestamptz not null default now(),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint sample_documents_only check (is_sample)
);
comment on table public.documents is 'Sample document. SYNTHETIC ONLY - real identity documents must not be uploaded.';

-- ---------------------------------------------------------------------------
-- Assessment runs, facts, findings
-- ---------------------------------------------------------------------------

create table public.assessment_runs (
  id                 uuid primary key default gen_random_uuid(),
  application_id     uuid not null references public.applications (id) on delete cascade,
  rule_pack_id       uuid not null references public.rule_packs (id),
  rule_pack_version  text not null default '',  -- stamped by trigger from rule_packs.version
  model_name         text not null,
  prompt_version     text not null,
  consistency_check  boolean not null default false,
  -- Instruction-like text found in the application; surfaced to the officer, never obeyed.
  injection_flags    jsonb not null default '[]'::jsonb,
  triggered_by       uuid references auth.users (id),
  started_at         timestamptz not null default now(),
  finished_at        timestamptz,
  status             text not null default 'running' check (status in ('running', 'complete', 'failed')),
  error_message      text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint finished_when_done check (status = 'running' or finished_at is not null),
  constraint failed_has_error check (status <> 'failed' or error_message is not null)
);

create table public.fact_extractions (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  run_id          uuid not null references public.assessment_runs (id) on delete cascade,
  fact_key        text not null,   -- e.g. entity_type, incorporation_act, arrival_date
  fact_value      text not null,   -- or the literal 'not stated'
  source_quote    text,
  quote_verified  boolean not null default false,
  source          text,            -- form field name or document id
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (run_id, fact_key, source),
  constraint not_stated_has_no_quote check (fact_value <> 'not stated' or source_quote is null),
  constraint no_quote_not_verified check (source_quote is not null or not quote_verified)
);

create table public.findings (
  id                             uuid primary key default gen_random_uuid(),
  run_id                         uuid not null references public.assessment_runs (id) on delete cascade,
  application_id                 uuid not null references public.applications (id) on delete cascade,
  rule_id                        uuid not null references public.rules (id),
  ai_status                      text not null
                                   check (ai_status in ('Met', 'Not met', 'Needs evidence', 'Unclear', 'Evidence only')),
  rationale                      text,
  evidence_quote                 text,
  quote_verified                 boolean not null default false,
  -- Judgement / human_only rules: [{"quote": "...", "verified": true}, ...]
  supporting_quotes              jsonb not null default '[]'::jsonb,
  confidence                     text check (confidence in ('high', 'medium', 'low')),
  language_flag                  boolean not null default false,
  needs_applicant_clarification  boolean not null default false,
  is_valid                       boolean not null default false,
  -- LLM error / timeout / malformed output. Never silently becomes 'Met'.
  error_flag                     boolean not null default false,
  error_detail                   text,
  check_source                   text not null check (check_source in ('llm', 'code', 'human_only')),
  created_at                     timestamptz not null default now(),
  updated_at                     timestamptz not null default now(),
  unique (run_id, rule_id),
  -- Principle 5: a language obstacle is Unclear, never Not met.
  constraint language_flag_is_unclear check (not language_flag or ai_status = 'Unclear'),
  -- Principle 3: failure never looks like success.
  constraint error_is_unclear_and_invalid
    check (not error_flag or (ai_status in ('Unclear', 'Evidence only') and not is_valid)),
  constraint no_quote_not_verified check (evidence_quote is not null or not quote_verified),
  -- Principle 2: an unverified quote can never be valid.
  constraint valid_requires_verified_quote check (not is_valid or evidence_quote is null or quote_verified),
  -- An LLM Met / Not met with no quote is not evidence-backed, so it cannot be valid.
  constraint llm_decision_needs_quote
    check (not (is_valid and check_source = 'llm' and ai_status in ('Met', 'Not met') and evidence_quote is null)),
  -- Principle 6: Evidence only carries no conclusion, so no confidence either.
  constraint evidence_only_no_confidence check (ai_status <> 'Evidence only' or confidence is null)
);

-- ---------------------------------------------------------------------------
-- Officer workflow
-- ---------------------------------------------------------------------------

-- Append-only history: a later review for the same finding supersedes an
-- earlier one. Reviews are never edited in place.
create table public.officer_reviews (
  id            uuid primary key default gen_random_uuid(),
  -- Strictly increasing; defines which review is "latest" (timestamps can tie
  -- inside one transaction).
  seq           bigint generated always as identity unique,
  finding_id    uuid not null references public.findings (id) on delete cascade,
  officer_id    uuid not null references auth.users (id),
  action        text not null check (action in ('confirm', 'override', 'ask_applicant')),
  -- 'Evidence only' is never a final outcome: the officer decides.
  final_status  text check (final_status in ('Met', 'Not met', 'Needs evidence', 'Unclear')),
  reason        text,
  reviewed_at   timestamptz not null default now(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint decision_has_status
    check (action = 'ask_applicant' or final_status is not null),
  constraint ask_has_no_status
    check (action <> 'ask_applicant' or final_status is null),
  -- An override, or any 'Not met', needs a typed reason.
  constraint reason_required
    check ((action <> 'override' and final_status is distinct from 'Not met')
           or length(btrim(coalesce(reason, ''))) > 0)
);

create table public.clarification_requests (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  finding_id      uuid references public.findings (id) on delete set null,
  message_text    text not null,
  status          text not null default 'draft' check (status in ('draft', 'sent')),  -- 'sent' is a mock action
  created_by      uuid not null references auth.users (id),
  sent_at         timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint sent_has_timestamp check (status = 'draft' or sent_at is not null)
);

create table public.letters (
  id                  uuid primary key default gen_random_uuid(),
  application_id      uuid not null references public.applications (id) on delete cascade,
  version             integer not null,
  body_text           text not null,
  reading_grade       numeric,
  quality_checks      jsonb not null default '{}'::jsonb,
  -- Findings (all officer-confirmed) the letter was generated from.
  source_finding_ids  uuid[] not null default '{}',
  status              text not null default 'draft' check (status in ('draft', 'edited', 'approved')),
  edited_by           uuid references auth.users (id),
  approved_by         uuid references auth.users (id),
  approved_at         timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (application_id, version),
  constraint approval_recorded
    check (status <> 'approved' or (approved_by is not null and approved_at is not null))
);

create table public.sign_offs (
  id                      uuid primary key default gen_random_uuid(),
  application_id          uuid not null unique references public.applications (id) on delete cascade,
  officer_id              uuid not null references auth.users (id),
  signed_at               timestamptz not null default now(),
  statement_acknowledged  boolean not null,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  constraint statement_must_be_acknowledged check (statement_acknowledged)
);

-- ---------------------------------------------------------------------------
-- Cross-application register (synthetic)
-- ---------------------------------------------------------------------------

create table public.mock_grants_register (
  id                uuid primary key default gen_random_uuid(),
  applicant_id      uuid not null references public.applicants (id),
  grant_program_id  uuid not null references public.grant_programs (id),
  status            text not null check (status in ('active', 'closed')),
  start_date        date not null,
  end_date          date,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint dates_ordered check (end_date is null or end_date >= start_date)
);
comment on table public.mock_grants_register is
  'SYNTHETIC mock register for cross-application rules (e.g. max active grants). Not a real register.';

-- ---------------------------------------------------------------------------
-- Evaluation
-- ---------------------------------------------------------------------------

create table public.evaluation_cases (
  id                  uuid primary key default gen_random_uuid(),
  case_code           text not null unique,
  application_id      uuid not null references public.applications (id) on delete cascade,
  family_id           text,   -- groups twin versions of the same facts
  language_style_tag  text check (language_style_tag in ('polished', 'plain', 'second_language')),
  notes               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create table public.answer_key (
  id                  uuid primary key default gen_random_uuid(),
  evaluation_case_id  uuid not null references public.evaluation_cases (id) on delete cascade,
  rule_id             uuid not null references public.rules (id),
  expected_status     text not null
                        check (expected_status in ('Met', 'Not met', 'Needs evidence', 'Unclear', 'Evidence only')),
  notes               text,
  written_by          text not null,  -- human author; never generated from model output
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (evaluation_case_id, rule_id)
);
comment on table public.answer_key is
  'Written by humans from the published guidelines. Never generated from or edited to match model output.';

create table public.evaluation_runs (
  id               uuid primary key default gen_random_uuid(),
  model_name       text not null,
  prompt_version   text not null,
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  summary          jsonb not null default '{}'::jsonb,
  report_markdown  text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table public.evaluation_results (
  id                  uuid primary key default gen_random_uuid(),
  eval_run_id         uuid not null references public.evaluation_runs (id) on delete cascade,
  evaluation_case_id  uuid not null references public.evaluation_cases (id) on delete cascade,
  rule_id             uuid not null references public.rules (id),
  predicted_status    text,
  expected_status     text,
  correct             boolean not null,
  quote_valid         boolean,
  twin_consistent     boolean,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (eval_run_id, evaluation_case_id, rule_id)
);

-- ---------------------------------------------------------------------------
-- LLM response cache (backend only). Subject to the retention setting.
-- ---------------------------------------------------------------------------

create table public.llm_cache (
  -- sha256(application_text + rule_pack_version + prompt_version + model [+ call key])
  cache_key          text primary key,
  provider           text not null,
  model_name         text not null,
  prompt_version     text not null,
  rule_pack_version  text not null,
  response           jsonb not null,
  created_at         timestamptz not null default now(),
  expires_at         timestamptz
);
comment on table public.llm_cache is
  'Validated LLM outputs only (no prompts, no API keys). Purged by private.purge_expired_llm_data().';

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

create index on public.profiles (organisation_id);
create index on public.grant_programs (organisation_id);
create index on public.rule_packs (grant_program_id, status);
create index on public.rules (rule_pack_id, display_order);
create index on public.applicants (user_id);
create index on public.applications (applicant_id);
create index on public.applications (grant_program_id);
create index on public.applications (rule_pack_id);
create index on public.applications (status);
create index on public.documents (application_id);
create index on public.assessment_runs (application_id, status, finished_at desc);
create index on public.assessment_runs (status);
create index on public.fact_extractions (application_id);
create index on public.fact_extractions (run_id);
create index on public.findings (application_id);
create index on public.findings (run_id);
create index on public.findings (rule_id);
create index on public.findings (ai_status);
create index on public.officer_reviews (finding_id, seq desc);
create index on public.officer_reviews (officer_id);
create index on public.clarification_requests (application_id);
create index on public.clarification_requests (status);
create index on public.letters (application_id);
create index on public.letters (status);
create index on public.mock_grants_register (applicant_id, status);
create index on public.evaluation_cases (application_id);
create index on public.evaluation_cases (family_id);
create index on public.answer_key (rule_id);
create index on public.evaluation_results (eval_run_id);
create index on public.evaluation_results (rule_id);
create index on public.llm_cache (expires_at);

-- ---------------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------------

do $$
declare t text;
begin
  foreach t in array array[
    'organisations', 'profiles', 'grant_programs', 'rule_packs', 'rules', 'applicants',
    'applications', 'redaction_maps', 'documents', 'assessment_runs', 'fact_extractions',
    'findings', 'officer_reviews', 'clarification_requests', 'letters', 'sign_offs',
    'mock_grants_register', 'evaluation_cases', 'answer_key', 'evaluation_runs',
    'evaluation_results'
  ] loop
    execute format(
      'create trigger set_updated_at before update on public.%I
         for each row execute function private.set_updated_at()', t);
  end loop;
end;
$$;
