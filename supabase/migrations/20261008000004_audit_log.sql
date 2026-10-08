-- =============================================================================
-- 0004 Audit log (append-only).
--
-- Written only by the backend using the service role. No role - not even the
-- service role or the table owner - can UPDATE, DELETE or TRUNCATE it:
-- triggers block those for everyone, and privileges are revoked as a second
-- layer.
--
-- application_id / rule_id are deliberately NOT foreign keys: the audit trail
-- must outlive the rows it describes and must never block or cascade a delete.
-- There is no updated_at column because rows are never updated.
-- =============================================================================

create table public.audit_log (
  id                 uuid primary key default gen_random_uuid(),
  occurred_at        timestamptz not null default now(),
  actor_id           uuid,          -- auth user, or null for system actions
  actor_role         text not null, -- officer | applicant | admin | system
  action             text not null, -- e.g. finding.review, application.signoff
  application_id     uuid,
  rule_id            uuid,
  ai_suggestion      text,
  officer_decision   text,
  overridden         boolean not null default false,
  reason             text,
  rule_pack_version  text,
  details            jsonb not null default '{}'::jsonb,
  created_at         timestamptz not null default now()
);

comment on table public.audit_log is
  'Append-only. Must not contain full application text or personal data in details.';

create index on public.audit_log (application_id);
create index on public.audit_log (rule_id);
create index on public.audit_log (occurred_at desc);
create index on public.audit_log (action);
create index on public.audit_log (actor_id);

create or replace function private.audit_log_is_append_only()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_log is append-only: % is not allowed', tg_op
    using errcode = 'insufficient_privilege';
end;
$$;

create trigger audit_log_no_update
  before update on public.audit_log
  for each row execute function private.audit_log_is_append_only();

create trigger audit_log_no_delete
  before delete on public.audit_log
  for each row execute function private.audit_log_is_append_only();

create trigger audit_log_no_truncate
  before truncate on public.audit_log
  for each statement execute function private.audit_log_is_append_only();

-- Privileges: clients may at most read (RLS decides which rows); only the
-- service role may insert.
revoke all on public.audit_log from public, anon, authenticated, service_role;
grant select on public.audit_log to authenticated;
grant select, insert on public.audit_log to service_role;
