-- =============================================================================
-- 0001 Foundation: extensions, private helper schema, updated_at trigger.
--
-- The `private` schema is NOT exposed through the Supabase REST API (only
-- `public` is), so helper functions here cannot be called as RPCs by clients.
-- =============================================================================

create extension if not exists pgcrypto;

create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated, service_role;

-- Generic updated_at maintenance.
create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- True when the current statement is executed by the backend service role or
-- by a migration/seed connection (postgres). Must only be called from
-- SECURITY INVOKER contexts: inside a SECURITY DEFINER function current_user
-- is the function owner, which would always look privileged.
create or replace function private.is_service()
returns boolean
language sql
stable
set search_path = ''
as $$
  select current_user in ('postgres', 'service_role', 'supabase_admin')
      or coalesce(
           nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role',
           ''
         ) = 'service_role';
$$;

-- Parse text as uuid, returning null instead of raising (used on storage paths).
create or replace function private.try_uuid(p text)
returns uuid
language plpgsql
immutable
set search_path = ''
as $$
begin
  return p::uuid;
exception when others then
  return null;
end;
$$;
