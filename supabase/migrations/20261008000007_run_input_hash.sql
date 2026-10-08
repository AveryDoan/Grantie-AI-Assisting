-- =============================================================================
-- 0007 Idempotent assessment.
--
-- input_hash = sha256(redacted_text + rule_pack_version + prompt_version + model).
-- POST /applications/{id}/assess returns the latest complete run when the
-- hash is unchanged, so re-clicking "assess" does not create a new run and
-- does not discard the officer's reviews.
-- =============================================================================

alter table public.assessment_runs add column input_hash text;

create index on public.assessment_runs (application_id, input_hash);

-- Allow the backend to record the hash on an existing run but never change it.
create or replace function private.guard_run_input_hash()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.input_hash is not null and new.input_hash is distinct from old.input_hash then
    raise exception 'input_hash is immutable once set';
  end if;
  return new;
end;
$$;

create trigger guard_run_input_hash
  before update on public.assessment_runs
  for each row execute function private.guard_run_input_hash();
