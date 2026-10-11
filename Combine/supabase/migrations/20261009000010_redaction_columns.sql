-- =============================================================================
-- 0010 Redaction columns on applications and documents.
--
-- applications.redacted_text   what the LLM may see (already existed)
-- applications.location_class  coarse location computed before redaction
-- applications.ai_status       whether the LLM may be called for this application
-- documents.redacted_text / extraction_status / needs_manual_review
-- redaction_runs.input_hash    makes POST /redact idempotent
--
-- All of these are written by the backend service role only.
-- =============================================================================

alter table public.applications
  add column location_class text
    check (location_class in ('outside_australia', 'nt_australia', 'australia_outside_nt', 'unknown')),
  add column ai_status text not null default 'not_redacted'
    check (ai_status in ('not_redacted', 'ready', 'blocked_redaction_leak', 'blocked_low_confidence'));
comment on column public.applications.ai_status is
  'ready = redacted and leak-scanned; the LLM may be called. Anything else: the LLM must not be called.';

alter table public.documents
  add column redacted_text text,
  add column extraction_status text check (extraction_status in ('ok', 'no_text', 'unsupported')),
  add column needs_manual_review boolean not null default false;

alter table public.redaction_runs add column input_hash text;
create index on public.redaction_runs (application_id, input_hash);
create index on public.applications (ai_status);

-- Applicants and staff may not set the redaction outputs: extend the existing guards.
create or replace function private.guard_redaction_columns()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.is_service() then
    return new;
  end if;
  if tg_op = 'INSERT' then
    if new.ai_status <> 'not_redacted' or new.location_class is not null then
      raise exception 'Redaction fields are set by the system';
    end if;
  elsif new.ai_status is distinct from old.ai_status or new.location_class is distinct from old.location_class then
    raise exception 'Redaction fields are set by the system';
  end if;
  return new;
end;
$$;

create trigger guard_redaction_columns
  before insert or update on public.applications
  for each row execute function private.guard_redaction_columns();

create or replace function private.guard_document_redaction()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.is_service() then
    return new;
  end if;
  if tg_op = 'INSERT' then
    if new.redacted_text is not null or new.extraction_status is not null or new.needs_manual_review then
      raise exception 'Document redaction fields are set by the system';
    end if;
  elsif new.redacted_text is distinct from old.redacted_text
     or new.extraction_status is distinct from old.extraction_status
     or new.needs_manual_review is distinct from old.needs_manual_review then
    raise exception 'Document redaction fields are set by the system';
  end if;
  return new;
end;
$$;

create trigger guard_document_redaction
  before insert or update on public.documents
  for each row execute function private.guard_document_redaction();
