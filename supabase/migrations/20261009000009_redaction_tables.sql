-- =============================================================================
-- 0009 Redaction runs and the token map.
--
-- The LLM only ever receives redacted text. The token map links tokens such
-- as [PERSON_1] back to the originals so OFFICERS can see the real wording.
--
-- original_value_encrypted is encrypted in the API (AES-256-GCM, key from the
-- REDACTION_KEY environment variable) before it reaches the database - the
-- database never sees the key or the plaintext. spans holds positions only.
--
-- Access: officers/admins of the application's organisation may read; only
-- the backend service role writes. Applicants cannot read it. The LLM never
-- receives it (it is never part of a prompt).
-- =============================================================================

create table public.redaction_runs (
  id                uuid primary key default gen_random_uuid(),
  application_id    uuid not null references public.applications (id) on delete cascade,
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  status            text not null check (status in ('running', 'ok', 'blocked_redaction_leak', 'needs_manual_review')),
  counts            jsonb not null default '{}'::jsonb,   -- replacements per entity type; never values
  leak_scan         jsonb not null default '{}'::jsonb,   -- check name -> count; never values
  detector_version  text not null,
  config_hash       text not null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
comment on column public.redaction_runs.counts is 'Number of replacements per entity type. Must never contain personal values.';

create table public.redaction_token_maps (
  id                        uuid primary key default gen_random_uuid(),
  application_id            uuid not null references public.applications (id) on delete cascade,
  run_id                    uuid references public.redaction_runs (id) on delete set null,
  token                     text not null,          -- e.g. [PERSON_1], [LOCATION: outside Australia]
  entity_type               text not null,          -- PERSON, REFEREE, EMAIL, PHONE, ADDRESS, PLACE, DOB, ID, URL, HANDLE
  original_value_encrypted  text not null check (original_value_encrypted like 'v1:%'),
  source                    text not null,          -- field name or document id (never a value)
  text_key                  text not null,          -- which stored text the spans refer to
  spans                     jsonb not null default '[]'::jsonb,  -- [[red_start, red_end, orig_start, orig_end], ...]
  ordinal                   integer not null,       -- order of first appearance
  created_at                timestamptz not null default now()
);
comment on table public.redaction_token_maps is
  'Token -> encrypted original. Officers and the service role only. Never sent to an LLM. Never logged.';

create index on public.redaction_runs (application_id, started_at desc);
create index on public.redaction_runs (status);
create index on public.redaction_token_maps (application_id);
create index on public.redaction_token_maps (application_id, token);

create trigger set_updated_at before update on public.redaction_runs
  for each row execute function private.set_updated_at();

alter table public.redaction_runs enable row level security;
alter table public.redaction_token_maps enable row level security;

revoke all on public.redaction_runs, public.redaction_token_maps from anon;
-- Clients may at most read (RLS decides which rows); writes are service-role only.
revoke insert, update, delete, truncate on public.redaction_runs, public.redaction_token_maps from authenticated;

create policy redaction_runs_staff_select on public.redaction_runs
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy redaction_token_maps_staff_select on public.redaction_token_maps
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));
