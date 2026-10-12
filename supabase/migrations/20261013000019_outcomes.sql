-- =============================================================================
-- 0019 Outcomes that end with a letter: Ineligible (officer decision) and Not selected (competitive).
--
-- A. Ineligible: an officer confirms a rule as Not met and may go straight to Outcome. The steps the decline does not need
--    (the rest of eligibility, consistency, the AI-use check, merit) are recorded in steps_not_assessed with who and when.
-- B. Not selected (competitive): PEOPLE decide who is funded, outside this tool. round_decisions records that decision
--    (who decided, under what authority, where it is minuted, and the officer's reason note). Nothing here computes, orders
--    or compares applications.
--
-- Fail closed, for every caller including the service role the API uses:
--   1. outcome_status / eligibility_result = 'ineligible' needs an officer-confirmed Not met finding.
--   2. a decline letter may only cite officer-confirmed Not met findings.
--   3. a round decision needs eligibility_result = 'eligible' and complete merit (each criterion marked with a reason, or
--      Not assessed with a reason).
--   4. a not_selected letter needs a not_selected round decision with authority, reference and reason note.
--   5. released_at needs signed_off_by, signed_off_at, letter_viewed_at and checks_passed.
--   6. after release nothing changes, except through a reconsideration (a reopened sign-off, sign_off_history).
--   7. sign-offs, officer decisions, round decisions, releases and "not assessed" records must name a real officer
--      (profiles.role = 'officer'). The AI and system jobs have no officer profile, so they cannot do any of these.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Columns
-- ---------------------------------------------------------------------------

alter table public.applications
  add column outcome_status text not null default 'in_progress'
    check (outcome_status in ('in_progress', 'ineligible', 'eligible_awaiting_round_decision', 'selected',
                              'not_selected_competitive', 'needs_info', 'withdrawn')),
  add column eligibility_result text check (eligibility_result in ('eligible', 'ineligible'));

alter table public.letters drop constraint if exists letters_kind_check;
alter table public.letters
  add constraint letters_kind_check check (kind in ('decline', 'next_steps', 'not_selected')),
  add column letter_viewed_at timestamptz,
  add column viewed_by        uuid references auth.users (id),
  add column checks_passed    boolean not null default false,
  add column template         boolean not null default false,   -- built by code from a fixed template (no model)
  add column signed_off_by    uuid references auth.users (id),
  add column signed_off_at    timestamptz,
  add column released_at      timestamptz,
  add column released_by      uuid references auth.users (id);

create table public.steps_not_assessed (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  step            text not null check (step in ('eligibility', 'consistency', 'ai_use', 'merit')),
  reason          text not null check (length(btrim(reason)) > 0),
  officer_id      uuid not null references auth.users (id),
  created_at      timestamptz not null default now(),
  cleared_at      timestamptz,             -- the officer reversed the Not met before sign-off: the steps are needed again
  cleared_by      uuid references auth.users (id)
);
create index on public.steps_not_assessed (application_id);

create table public.round_decisions (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  version         integer not null default 1,
  decision        text not null check (decision in ('selected', 'not_selected_competitive')),
  decided_by      text not null check (length(btrim(decided_by)) > 0),      -- e.g. "Assessment panel"
  authority       text not null check (length(btrim(authority)) > 0),       -- the panel or role that has the authority
  decided_at      date not null,
  reference       text not null check (length(btrim(reference)) > 0),       -- where it is minuted
  reason_note     text,
  created_by      uuid not null references auth.users (id),                 -- the officer who recorded it
  created_at      timestamptz not null default now(),
  unique (application_id, version),
  constraint not_selected_has_reason check (decision = 'selected' or length(btrim(coalesce(reason_note, ''))) >= 20)
);
comment on table public.round_decisions is
  'The decision people made about funding (selected / not selected). Recorded here, never computed. No rank, mark or order.';

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

create or replace function private.is_officer(p_user uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.profiles p where p.id = p_user and p.role = 'officer');
$$;

-- Findings of the latest complete run that an officer has confirmed (or overridden to) Not met. Eligibility rules only.
create or replace function private.confirmed_not_met(p_application uuid)
returns uuid[]
language sql stable security definer
set search_path = ''
as $$
  select coalesce(array_agg(f.id), '{}')
  from public.findings f
  join public.rules ru on ru.id = f.rule_id
  join lateral (
    select o.action, o.final_status from public.officer_reviews o where o.finding_id = f.id order by o.seq desc limit 1
  ) latest on true
  where f.run_id = private.latest_complete_run(p_application)
    and coalesce(ru.params->>'section', '') <> 'merit'
    and latest.action in ('confirm', 'override') and latest.final_status = 'Not met';
$$;

create or replace function private.on_ineligible_path(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.steps_not_assessed s where s.application_id = p_application and s.cleared_at is null)
     and cardinality(private.confirmed_not_met(p_application)) > 0;
$$;

-- Every eligibility finding of the latest run decided Met by an officer.
create or replace function private.all_confirmed_met(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.findings f where f.run_id = private.latest_complete_run(p_application))
     and not exists (
       select 1
       from public.findings f
       join public.rules ru on ru.id = f.rule_id
       left join lateral (
         select o.action, o.final_status from public.officer_reviews o where o.finding_id = f.id order by o.seq desc limit 1
       ) latest on true
       where f.run_id = private.latest_complete_run(p_application)
         and coalesce(ru.params->>'section', '') <> 'merit'
         and (latest.action is null or latest.action = 'ask_applicant' or latest.final_status <> 'Met'));
$$;

-- Every merit criterion of the latest run's pack is marked with a reason, or Not assessed with a reason.
create or replace function private.merit_complete(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select private.latest_complete_run(p_application) is not null and not exists (
    select 1
    from public.rules ru
    join public.assessment_runs r on r.rule_pack_id = ru.rule_pack_id and r.id = private.latest_complete_run(p_application)
    where ru.params->>'section' = 'merit'
      and not exists (
        select 1 from public.merit_marks m
        where m.application_id = p_application and m.rule_code = ru.rule_code
          and length(btrim(coalesce(m.reason, ''))) > 0));
$$;

-- A released letter can only be followed by a change after a reconsideration (a reopened sign-off) later than the release.
create or replace function private.reconsidered_since_release(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select not exists (select 1 from public.letters l where l.application_id = p_application and l.released_at is not null)
      or exists (
        select 1 from public.sign_off_history h
        where h.application_id = p_application
          and h.reopened_at > (select max(l.released_at) from public.letters l where l.application_id = p_application));
$$;

grant execute on function private.is_officer(uuid), private.confirmed_not_met(uuid), private.on_ineligible_path(uuid),
  private.all_confirmed_met(uuid), private.merit_complete(uuid), private.reconsidered_since_release(uuid)
  to authenticated, service_role;

-- On the ineligible path the steps the decline does not need are recorded as not assessed, so they do not block sign-off.
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
  if private.on_ineligible_path(p_application) then
    return 0;     -- a confirmed Not met; the remaining checks were recorded as not assessed (ineligible)
  end if;
  select r.rule_pack_id into v_pack from public.assessment_runs r where r.id = v_run;

  select count(*) into v_missing
  from public.rules ru
  where ru.rule_pack_id = v_pack
    and not exists (select 1 from public.findings f where f.run_id = v_run and f.rule_id = ru.id);

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

-- ---------------------------------------------------------------------------
-- 1. Outcome and eligibility result on the application
-- ---------------------------------------------------------------------------

create or replace function private.guard_application_outcome()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.outcome_status is not distinct from old.outcome_status and new.eligibility_result is not distinct from old.eligibility_result then
    return new;
  end if;
  if not private.is_service() then
    raise exception 'outcome_status and eligibility_result are set by the officer''s recorded actions, not directly';
  end if;
  if (new.outcome_status = 'ineligible' or new.eligibility_result = 'ineligible')
     and cardinality(private.confirmed_not_met(new.id)) = 0 then
    raise exception 'OUTCOME_BLOCKED: ineligible needs at least one finding an officer confirmed as Not met';
  end if;
  if new.eligibility_result = 'eligible' and not private.all_confirmed_met(new.id) then
    raise exception 'OUTCOME_BLOCKED: eligible needs every eligibility finding decided Met by an officer';
  end if;
  if new.outcome_status in ('selected', 'not_selected_competitive') and not exists (
       select 1 from public.round_decisions d
       where d.application_id = new.id and d.decision = new.outcome_status
         and d.version = (select max(x.version) from public.round_decisions x where x.application_id = new.id)) then
    raise exception 'OUTCOME_BLOCKED: % needs a recorded round decision', new.outcome_status;
  end if;
  return new;
end;
$$;

create trigger guard_application_outcome
  before update on public.applications
  for each row execute function private.guard_application_outcome();

-- ---------------------------------------------------------------------------
-- Steps not assessed (ineligible path)
-- ---------------------------------------------------------------------------

create or replace function private.guard_steps_not_assessed()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'steps_not_assessed is a record: rows are cleared, never deleted';
  end if;
  if tg_op = 'INSERT' then
    if not private.is_officer(new.officer_id) then
      raise exception 'OFFICER_ONLY: only an officer can record steps as not assessed';
    end if;
    if cardinality(private.confirmed_not_met(new.application_id)) = 0 then
      raise exception 'OUTCOME_BLOCKED: steps can be skipped only after an officer confirmed a rule as Not met';
    end if;
    if new.cleared_at is not null then
      raise exception 'A new record cannot start cleared';
    end if;
    return new;
  end if;
  -- UPDATE: only clearing, once, by an officer.
  if old.cleared_at is not null then
    raise exception 'This record was already cleared';
  end if;
  if new.application_id <> old.application_id or new.step <> old.step or new.reason <> old.reason or new.officer_id <> old.officer_id
     or new.created_at <> old.created_at or new.cleared_at is null or not private.is_officer(new.cleared_by) then
    raise exception 'Only clearing (by an officer) is allowed';
  end if;
  return new;
end;
$$;

create trigger guard_steps_not_assessed
  before insert or update or delete on public.steps_not_assessed
  for each row execute function private.guard_steps_not_assessed();

-- ---------------------------------------------------------------------------
-- 3 and 6. Round decisions: recorded by an officer, only for eligible applications with complete merit; append-only
-- ---------------------------------------------------------------------------

create or replace function private.guard_round_decision()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if tg_op <> 'INSERT' then
    raise exception 'Round decisions are append-only: record a new version instead';
  end if;
  if not private.is_officer(new.created_by) then
    raise exception 'OFFICER_ONLY: only an officer can record a round decision';
  end if;
  if (select a.eligibility_result from public.applications a where a.id = new.application_id) is distinct from 'eligible' then
    raise exception 'ROUND_DECISION_BLOCKED: the application is not confirmed eligible by an officer';
  end if;
  if not private.merit_complete(new.application_id) then
    raise exception 'ROUND_DECISION_BLOCKED: every merit criterion needs a mark and reason, or Not assessed with a reason';
  end if;
  if (select a.status from public.applications a where a.id = new.application_id) = 'signed_off'
     or not private.reconsidered_since_release(new.application_id) then
    raise exception 'ROUND_DECISION_BLOCKED: the outcome is signed off; record a reconsideration first';
  end if;
  new.version := coalesce((select max(d.version) from public.round_decisions d where d.application_id = new.application_id), 0) + 1;
  return new;
end;
$$;

create trigger guard_round_decision
  before insert or update or delete on public.round_decisions
  for each row execute function private.guard_round_decision();

-- ---------------------------------------------------------------------------
-- 2, 4, 5 and 6. Letters
-- ---------------------------------------------------------------------------

create or replace function private.guard_letter_outcome()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_ok uuid[];
begin
  if tg_op = 'DELETE' then
    if old.released_at is not null then
      raise exception 'A released letter cannot be deleted';
    end if;
    return old;
  end if;
  if tg_op = 'UPDATE' and old.released_at is not null then
    raise exception 'Letter version % was released and cannot be changed; a change needs a reconsideration and a new version', old.version;
  end if;

  -- 2. A decline letter cites only officer-confirmed Not met findings (checked when written and again when released).
  if new.kind = 'decline' and (tg_op = 'INSERT' or new.released_at is not null) then
    v_ok := private.confirmed_not_met(new.application_id);
    if cardinality(new.source_finding_ids) = 0 or not (new.source_finding_ids <@ v_ok) then
      raise exception 'LETTER_BLOCKED: a decline letter may only cite findings an officer confirmed as Not met';
    end if;
  end if;

  -- 4. A not-selected letter needs the people's decision on record, with its authority, reference and reason.
  if new.kind = 'not_selected' and tg_op = 'INSERT' and not exists (
       select 1 from public.round_decisions d
       where d.application_id = new.application_id and d.decision = 'not_selected_competitive'
         and length(btrim(coalesce(d.reason_note, ''))) >= 20
         and d.version = (select max(x.version) from public.round_decisions x where x.application_id = new.application_id)) then
    raise exception 'LETTER_BLOCKED: a not-selected letter needs a recorded round decision with a reason note';
  end if;

  -- 7. The officer who viewed, signed off or released is a real officer.
  if (new.viewed_by is not null and not private.is_officer(new.viewed_by))
     or (new.signed_off_by is not null and not private.is_officer(new.signed_off_by))
     or (new.released_by is not null and not private.is_officer(new.released_by)) then
    raise exception 'OFFICER_ONLY: only an officer can view, sign off or release a letter';
  end if;

  -- 5. Release needs sign-off, the officer having read it to the end, and passing checks. Approved = released.
  if new.released_at is not null and (new.signed_off_by is null or new.signed_off_at is null or new.letter_viewed_at is null
                                      or not new.checks_passed or new.released_by is null) then
    raise exception 'RELEASE_BLOCKED: a letter is released only after sign-off, after the officer read it, and when every check passes';
  end if;
  if (new.status = 'approved') <> (new.released_at is not null) then
    raise exception 'RELEASE_BLOCKED: approved and released go together';
  end if;
  return new;
end;
$$;

create trigger guard_letter_outcome
  before insert or update or delete on public.letters
  for each row execute function private.guard_letter_outcome();

-- ---------------------------------------------------------------------------
-- 7. Decisions and sign-offs name a real officer, whoever writes the row
-- ---------------------------------------------------------------------------

create or replace function private.guard_officer_identity()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if not private.is_officer(new.officer_id) then
    raise exception 'OFFICER_ONLY: % must be recorded by an officer', tg_table_name;
  end if;
  return new;
end;
$$;

create trigger guard_officer_identity before insert on public.officer_reviews
  for each row execute function private.guard_officer_identity();
create trigger guard_officer_identity before insert on public.sign_offs
  for each row execute function private.guard_officer_identity();

-- ---------------------------------------------------------------------------
-- RLS: staff read; every write goes through the API (which checks the officer) and the triggers above.
-- ---------------------------------------------------------------------------

do $$
declare t text;
begin
  foreach t in array array['steps_not_assessed', 'round_decisions'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke insert, update, delete, truncate on public.%I from authenticated', t);
    execute format('create policy %I on public.%I for select to authenticated using (private.is_staff() and private.can_staff_access(application_id))',
                   t || '_staff_select', t);
  end loop;
end $$;
