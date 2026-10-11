-- =============================================================================
-- 0014 Merit marks: the officer's own mark from 0 to 100 for each merit criterion.
--
-- The mark is typed by the officer. The AI never suggests, pre-fills or estimates it. A mark needs a reason.
-- A criterion can instead be set to "Not assessed". There is no total, average, weighted score or ranking: the table
-- holds one row per criterion and nothing here adds rows together. Every change is audited by the API (old and new value).
-- Sign-off is blocked until every merit criterion is either marked or set to "Not assessed".
-- =============================================================================

create table public.merit_marks (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  rule_code       text not null,
  mark            smallint check (mark between 0 and 100),
  not_assessed    boolean not null default false,
  reason          text,
  officer_id      uuid references auth.users (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (application_id, rule_code),
  constraint mark_or_not_assessed check (
    (mark is not null and not not_assessed and length(btrim(coalesce(reason, ''))) > 0)
    or (mark is null and not_assessed)
  )
);
comment on table public.merit_marks is
  'An officer''s mark (0 to 100) for one merit criterion, with a reason. Never AI-suggested, never totalled or compared.';

create trigger set_updated_at before update on public.merit_marks
  for each row execute function private.set_updated_at();

alter table public.merit_marks enable row level security;
revoke all on public.merit_marks from anon;
revoke insert, update, delete, truncate on public.merit_marks from authenticated;

create policy merit_marks_staff_select on public.merit_marks
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

-- The database guard for sign-off counts a merit criterion as decided only when it has a mark (or is Not assessed).
create or replace function private.unreviewed_findings(p_application uuid)
returns integer
language plpgsql stable security definer
set search_path = ''
as $$
declare
  v_run       uuid := private.latest_complete_run(p_application);
  v_pack      uuid;
  v_missing   integer;
  v_undecided integer;
begin
  if v_run is null then
    return null;  -- no complete run
  end if;
  select r.rule_pack_id into v_pack from public.assessment_runs r where r.id = v_run;

  -- Rules with no finding in this run count as unreviewed.
  select count(*) into v_missing
  from public.rules ru
  where ru.rule_pack_id = v_pack
    and not exists (select 1 from public.findings f where f.run_id = v_run and f.rule_id = ru.id);

  -- Merit criteria are decided by the officer's mark; every other rule by a review.
  select count(*) into v_undecided
  from public.findings f
  join public.rules ru on ru.id = f.rule_id
  left join lateral (
    select o.action from public.officer_reviews o
    where o.finding_id = f.id
    order by o.seq desc
    limit 1
  ) latest on true
  where f.run_id = v_run
    and case
      when ru.params->>'section' = 'merit'
        then not exists (select 1 from public.merit_marks m where m.application_id = p_application and m.rule_code = ru.rule_code)
      else (latest.action is null or latest.action = 'ask_applicant')
    end;

  return v_missing + v_undecided;
end;
$$;
