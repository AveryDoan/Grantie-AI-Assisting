-- =============================================================================
-- 0017 Merit summaries: what the AI found for each merit criterion, built from quotes that code verified.
--
-- Stage 2 (AI) extracts claims; stage 3 (code) verifies each quote; stage 4 (AI) writes bullets that cite claims; stage 5
-- (code) lists the passages no bullet used. Nothing here scores, ranks or compares: the officer's mark stays in merit_marks.
-- Every regeneration is a new version and the old one is kept. Quotes and passages are REDACTED text; the API restores the
-- applicant's own words only for display to staff.
-- =============================================================================

create table public.merit_runs (
  id                     uuid primary key default gen_random_uuid(),
  application_id         uuid not null references public.applications (id) on delete cascade,
  rule_code              text not null,
  version                integer not null,
  status                 text not null check (status in ('ok', 'no_ai', 'failed')),
  error                  text,
  model                  text,
  claims_prompt_version  text,
  summary_prompt_version text,
  coverage               jsonb not null default '[]',   -- per document: passages found, readable or not
  dropped                jsonb not null default '[]',   -- claims and bullets that failed verification ("Could not verify")
  overview               jsonb not null default '[]',   -- two or three plain sentences, checked by code like the bullets
  created_at             timestamptz not null default now(),
  unique (application_id, rule_code, version)
);

create table public.merit_claims (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  rule_code       text not null,
  version         integer not null,
  claim_id        text not null,
  source          text not null,                          -- 'application_text' or 'document:<id>'
  document_id     uuid,
  passage_id      text not null,
  quote           text not null,                          -- redacted words, verified to be in the document
  restatement     text not null,
  theme           text not null,
  subject         text not null default '',               -- who or what the fact is about: 'Documents differ' compares
  attribute       text not null default '',               -- only the same subject AND attribute across documents
  verified        boolean not null default true,
  created_at      timestamptz not null default now(),
  unique (application_id, rule_code, version, claim_id)
);

create table public.merit_summary_items (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  rule_code       text not null,
  version         integer not null,
  item_id         text not null,
  theme           text not null,
  text            text not null,
  claim_ids       jsonb not null,                         -- a bullet with no claim id is never stored
  documents_also  jsonb not null default '[]',
  differs         boolean not null default false,         -- decided by code: documents state different values
  position        integer not null,
  created_at      timestamptz not null default now(),
  unique (application_id, rule_code, version, item_id)
);

create table public.merit_coverage (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  rule_code       text not null,
  version         integer not null,
  passage_id      text not null,
  source          text not null,
  document_id     uuid,
  used            boolean not null default false,         -- false: listed under "Other passages found"
  span_start      integer not null,
  span_end        integer not null,
  page            integer not null default 1,
  text            text not null,
  created_at      timestamptz not null default now(),
  unique (application_id, rule_code, version, passage_id)
);

do $$
declare t text;
begin
  foreach t in array array['merit_runs', 'merit_claims', 'merit_summary_items', 'merit_coverage'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke insert, update, delete, truncate on public.%I from authenticated', t);
    execute format('create policy %I on public.%I for select to authenticated using (private.is_staff() and private.can_staff_access(application_id))',
                   t || '_staff_select', t);
  end loop;
end $$;

comment on table public.merit_summary_items is
  'A neutral summary bullet for one merit criterion. Every bullet cites verified claims. No score, strength or ranking.';
