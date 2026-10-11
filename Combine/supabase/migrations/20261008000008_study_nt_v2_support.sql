-- =============================================================================
-- 0008 Support for the Study NT rule pack v2.
--
-- 1. More sample document types (offer letter, travel booking, flight
--    screenshot, referee letter, headshot, certified translation).
-- 2. Reference lists (e.g. NT education providers, NT Skilled Occupation
--    Priority List). Loaded by an admin from the official source; rules that
--    need a list return "Unclear" until it is loaded - never "Met".
-- 3. The mock register holds grants, scholarships and applications, so the
--    cross-application rules (S10, S13, S14) can be checked by code.
-- =============================================================================

alter table public.documents drop constraint if exists documents_detected_type_check;
alter table public.documents add constraint documents_detected_type_check
  check (detected_type in ('coe', 'visa', 'travel_document', 'offer_letter', 'travel_booking',
                           'flight_screenshot', 'referee_letter', 'headshot', 'certified_translation', 'other'));

create table public.reference_lists (
  id           uuid primary key default gen_random_uuid(),
  name         text not null unique,          -- e.g. nt_education_providers
  description  text,
  items        jsonb not null default '[]'::jsonb,  -- array of strings
  source       text,                          -- where the official list came from
  loaded_by    uuid references auth.users (id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint items_is_array check (jsonb_typeof(items) = 'array')
);
comment on table public.reference_lists is
  'Official lookup lists used by code checks. Empty list = not loaded: dependent rules return Unclear.';

create trigger set_updated_at before update on public.reference_lists
  for each row execute function private.set_updated_at();

alter table public.reference_lists enable row level security;
create policy reference_lists_staff_select on public.reference_lists
  for select to authenticated using (private.is_staff());
create policy reference_lists_admin_write on public.reference_lists
  for all to authenticated using (private.is_admin()) with check (private.is_admin());
revoke all on public.reference_lists from anon;

alter table public.mock_grants_register
  add column record_type text not null default 'grant'
    check (record_type in ('grant', 'scholarship', 'application')),
  add column record_name text;
alter table public.mock_grants_register alter column grant_program_id drop not null;
comment on column public.mock_grants_register.record_type is
  'grant | scholarship | application (SYNTHETIC mock of the records an officer would check).';

create index on public.mock_grants_register (applicant_id, record_type);
