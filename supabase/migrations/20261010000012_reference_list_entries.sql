-- =============================================================================
-- 0012 Reference lists entered by officers.
--
-- `items` stays the plain list of names that rules match against. `entries`
-- keeps the full rows (OSCA code, occupation, skill level, tier) for display,
-- and `edition` records which published edition of the list was loaded.
-- Saving a list is done by the API (service role) and audited with counts only.
-- =============================================================================

alter table public.reference_lists
  add column entries jsonb not null default '[]'::jsonb,
  add column edition text;

alter table public.reference_lists
  add constraint reference_lists_entries_is_array check (jsonb_typeof(entries) = 'array');

comment on column public.reference_lists.entries is
  'Full rows of the list, for example {code, name, skill_level, tier}. The rules read items.';
comment on column public.reference_lists.edition is
  'Which published edition of the list this is, for example "31 August 2026".';
