-- =============================================================================
-- 0003 Identity helpers and integrity triggers.
--
-- The API enforces these rules too; the database repeats them so a bug in the
-- API, or a direct Supabase client call, cannot bypass them. Triggers that
-- check "nothing is missing" are SECURITY DEFINER so RLS cannot hide rows
-- from the check and make it pass by accident.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Identity / access helpers (SECURITY DEFINER: read profiles without RLS
-- recursion). All use auth.uid(), never current_user.
-- ---------------------------------------------------------------------------

create or replace function private.user_role()
returns text
language sql stable security definer
set search_path = ''
as $$
  select p.role from public.profiles p where p.id = auth.uid();
$$;

create or replace function private.user_org()
returns uuid
language sql stable security definer
set search_path = ''
as $$
  select p.organisation_id from public.profiles p where p.id = auth.uid();
$$;

create or replace function private.is_admin()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select coalesce(private.user_role() = 'admin', false);
$$;

create or replace function private.is_staff()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select coalesce(private.user_role() in ('officer', 'admin'), false);
$$;

-- Staff may access a grant program: admins everywhere, officers in their org.
create or replace function private.can_staff_access_program(p_program uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.grant_programs g
    join public.profiles p on p.id = auth.uid()
    where g.id = p_program
      and (p.role = 'admin' or (p.role = 'officer' and g.organisation_id = p.organisation_id))
  );
$$;

create or replace function private.can_staff_access(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.applications a
    where a.id = p_application
      and private.can_staff_access_program(a.grant_program_id)
  );
$$;

create or replace function private.is_applicant_owner(p_application uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.applications a
    join public.applicants ap on ap.id = a.applicant_id
    where a.id = p_application and ap.user_id = auth.uid()
  );
$$;

create or replace function private.owns_applicant(p_applicant uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.applicants ap where ap.id = p_applicant and ap.user_id = auth.uid()
  );
$$;

create or replace function private.staff_can_see_applicant(p_applicant uuid)
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select private.is_admin() or exists (
    select 1 from public.applications a
    where a.applicant_id = p_applicant and private.can_staff_access_program(a.grant_program_id)
  );
$$;

create or replace function private.application_status(p_application uuid)
returns text
language sql stable security definer
set search_path = ''
as $$
  select a.status from public.applications a where a.id = p_application;
$$;

-- The run whose findings are "current" for an application.
create or replace function private.latest_complete_run(p_application uuid)
returns uuid
language sql stable security definer
set search_path = ''
as $$
  select r.id
  from public.assessment_runs r
  where r.application_id = p_application and r.status = 'complete'
  order by r.finished_at desc, r.started_at desc
  limit 1;
$$;

grant execute on all functions in schema private to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- New auth users get an applicant profile. Role is NEVER read from signup
-- metadata; only an admin can promote a user.
-- ---------------------------------------------------------------------------

create or replace function private.handle_new_user()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, role, display_name)
  values (new.id, 'applicant', new.raw_user_meta_data ->> 'display_name')
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

-- ---------------------------------------------------------------------------
-- Rule packs: draft -> approved -> retired. Approved content is frozen, so
-- every run stamped with a version refers to exactly one set of rules.
-- ---------------------------------------------------------------------------

create or replace function private.guard_rule_pack()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' then
    if old.status <> 'draft' then
      raise exception 'Rule pack % is %; only draft packs can be deleted', old.version, old.status;
    end if;
    return old;
  end if;

  if tg_op = 'INSERT' then
    if new.status <> 'draft' then
      raise exception 'Rule packs must be created as draft and approved afterwards';
    end if;
    return new;
  end if;

  -- UPDATE
  if old.status = 'retired' then
    raise exception 'Rule pack % is retired and cannot be changed', old.version;
  end if;

  if old.status = 'approved' then
    if new.status <> 'retired'
       or (to_jsonb(new) - array['status', 'updated_at'])
          is distinct from (to_jsonb(old) - array['status', 'updated_at']) then
      raise exception 'Rule pack % is approved; it can only be retired. Create a new version instead.', old.version;
    end if;
    return new;
  end if;

  -- old.status = 'draft'
  if new.status = 'retired' then
    raise exception 'A draft rule pack cannot be retired; delete it instead';
  end if;
  if new.status = 'approved'
     and not exists (select 1 from public.rules r where r.rule_pack_id = new.id) then
    raise exception 'Cannot approve rule pack % with no rules', new.version;
  end if;
  return new;
end;
$$;

create trigger guard_rule_pack
  before insert or update or delete on public.rule_packs
  for each row execute function private.guard_rule_pack();

create or replace function private.guard_rules()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_status text;
begin
  select rp.status into v_status
  from public.rule_packs rp
  where rp.id = case when tg_op = 'DELETE' then old.rule_pack_id else new.rule_pack_id end;

  -- v_status is null when the whole pack is being deleted (cascade); allow.
  if v_status is not null and v_status <> 'draft' then
    raise exception 'Rules in an % rule pack cannot be changed', v_status;
  end if;
  if tg_op = 'UPDATE' and new.rule_pack_id <> old.rule_pack_id then
    raise exception 'Rules cannot move between rule packs';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

create trigger guard_rules
  before insert or update or delete on public.rules
  for each row execute function private.guard_rules();

-- ---------------------------------------------------------------------------
-- Applications
-- ---------------------------------------------------------------------------

-- Insert / program / pack consistency (applies to everyone, including seed).
create or replace function private.check_application_pack()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_pack record;
begin
  select rp.grant_program_id, rp.status into v_pack
  from public.rule_packs rp where rp.id = new.rule_pack_id;

  if v_pack.grant_program_id <> new.grant_program_id then
    raise exception 'Rule pack does not belong to the application''s grant program';
  end if;
  if tg_op = 'INSERT' and v_pack.status <> 'approved' then
    raise exception 'Applications can only be created against an approved rule pack';
  end if;
  return new;
end;
$$;

create trigger check_application_pack
  before insert or update of rule_pack_id, grant_program_id on public.applications
  for each row execute function private.check_application_pack();

-- Who may change what. SECURITY INVOKER so private.is_service() sees the
-- real caller.
create or replace function private.guard_application_update()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_role text := private.user_role();
begin
  -- Absolute, for every caller: an applicant's opt-out of AI assessment
  -- can never be reversed.
  if old.manual_assessment_requested and not new.manual_assessment_requested then
    raise exception 'manual_assessment_requested cannot be withdrawn once set';
  end if;

  if private.is_service() then
    return new;
  end if;

  if old.status = 'signed_off' then
    raise exception 'Application has been signed off and is read-only';
  end if;
  if new.status = 'signed_off' then
    raise exception 'Use a sign-off record to sign off an application';
  end if;
  if new.applicant_id <> old.applicant_id
     or new.redacted_text is distinct from old.redacted_text
     or new.language_style_tag is distinct from old.language_style_tag then
    raise exception 'These application fields are system-managed';
  end if;

  if v_role = 'applicant' then
    -- RLS already limits applicants to their own drafts.
    if new.status not in ('draft', 'submitted') then
      raise exception 'Applicants can only save a draft or submit it';
    end if;
    if new.status = 'submitted' then
      new.submitted_at := now();
    end if;
    return new;
  end if;

  if v_role in ('officer', 'admin') then
    if new.application_text is distinct from old.application_text
       or new.grant_program_id <> old.grant_program_id
       or new.rule_pack_id <> old.rule_pack_id
       or new.submitted_at is distinct from old.submitted_at then
      raise exception 'Staff cannot edit the applicant''s submission';
    end if;
    if new.manual_assessment_requested and not old.manual_assessment_requested then
      raise exception 'Only the applicant can request manual assessment';
    end if;
    if old.status = 'draft' and new.status <> 'draft' then
      raise exception 'Staff cannot submit an application on the applicant''s behalf';
    end if;
    return new;
  end if;

  raise exception 'Not permitted';
end;
$$;

create trigger guard_application_update
  before update on public.applications
  for each row execute function private.guard_application_update();

create or replace function private.guard_application_insert()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if private.is_service() then
    return new;
  end if;
  if new.status <> 'draft' or new.redacted_text is not null
     or new.language_style_tag is not null or new.manual_assessment_requested then
    raise exception 'New applications start as a plain draft';
  end if;
  return new;
end;
$$;

create trigger guard_application_insert
  before insert on public.applications
  for each row execute function private.guard_application_insert();

-- ---------------------------------------------------------------------------
-- Assessment runs: refuse to start on an opted-out application or on a pack
-- that is not approved; stamp the rule pack version.
-- ---------------------------------------------------------------------------

create or replace function private.guard_assessment_run()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_app  record;
  v_pack record;
begin
  if tg_op = 'UPDATE' then
    if new.application_id <> old.application_id
       or new.rule_pack_id <> old.rule_pack_id
       or new.rule_pack_version <> old.rule_pack_version then
      raise exception 'Run identity fields are immutable';
    end if;
    if old.status <> 'running' and new.status is distinct from old.status then
      raise exception 'A finished run cannot change status';
    end if;
    return new;
  end if;

  select a.manual_assessment_requested, a.rule_pack_id, a.status
    into v_app
  from public.applications a where a.id = new.application_id;

  if v_app.manual_assessment_requested then
    raise exception 'MANUAL_ASSESSMENT_REQUESTED: the applicant asked for a person to assess this application; AI assessment will not run'
      using errcode = 'P0001';
  end if;
  if v_app.status in ('draft', 'signed_off') then
    raise exception 'Cannot assess an application with status %', v_app.status;
  end if;
  if new.rule_pack_id <> v_app.rule_pack_id then
    raise exception 'Run must use the rule pack version recorded on the application';
  end if;

  select rp.status, rp.version into v_pack
  from public.rule_packs rp where rp.id = new.rule_pack_id;
  if v_pack.status <> 'approved' then
    raise exception 'RULE_PACK_NOT_APPROVED: rule pack % is %', v_pack.version, v_pack.status;
  end if;

  new.rule_pack_version := v_pack.version;
  new.status := 'running';
  new.finished_at := null;
  return new;
end;
$$;

create trigger guard_assessment_run
  before insert or update on public.assessment_runs
  for each row execute function private.guard_assessment_run();

-- ---------------------------------------------------------------------------
-- Findings: must match the run's pack; judgement / human_only rules carry
-- evidence only; frozen once an officer has reviewed them.
-- ---------------------------------------------------------------------------

create or replace function private.guard_finding()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_run  record;
  v_rule record;
begin
  if tg_op = 'UPDATE' and exists (
    select 1 from public.officer_reviews o where o.finding_id = old.id
  ) then
    raise exception 'Finding has been reviewed by an officer and is frozen';
  end if;

  select r.application_id, r.rule_pack_id, r.status into v_run
  from public.assessment_runs r where r.id = new.run_id;
  select ru.rule_pack_id, ru.rule_type, ru.check_method into v_rule
  from public.rules ru where ru.id = new.rule_id;

  if new.application_id <> v_run.application_id then
    raise exception 'Finding application does not match its run';
  end if;
  if v_rule.rule_pack_id <> v_run.rule_pack_id then
    raise exception 'Finding rule is not part of the run''s rule pack';
  end if;
  if new.check_source <> v_rule.check_method then
    raise exception 'check_source % does not match rule check_method %', new.check_source, v_rule.check_method;
  end if;

  -- Principle 6, both directions.
  if v_rule.rule_type = 'judgement' or v_rule.check_method = 'human_only' then
    if new.ai_status <> 'Evidence only' then
      raise exception 'Judgement / human-only rules may only return supporting evidence (status "Evidence only")';
    end if;
  elsif new.ai_status = 'Evidence only' then
    raise exception '"Evidence only" is reserved for judgement and human-only rules';
  end if;

  if tg_op = 'INSERT' and v_run.status <> 'running' then
    raise exception 'Findings can only be added while the run is running';
  end if;
  return new;
end;
$$;

create trigger guard_finding
  before insert or update on public.findings
  for each row execute function private.guard_finding();

-- ---------------------------------------------------------------------------
-- Officer reviews
-- ---------------------------------------------------------------------------

create or replace function private.guard_officer_review()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_f record;
begin
  if tg_op = 'UPDATE' then
    raise exception 'Officer reviews are append-only; record a new review instead';
  end if;

  select f.application_id, f.run_id, f.ai_status, f.is_valid into v_f
  from public.findings f where f.id = new.finding_id;

  if private.application_status(v_f.application_id) = 'signed_off' then
    raise exception 'Application has been signed off; findings can no longer be reviewed';
  end if;
  if v_f.run_id is distinct from private.latest_complete_run(v_f.application_id) then
    raise exception 'Only findings from the latest complete assessment run can be reviewed';
  end if;

  if new.action = 'confirm' then
    if v_f.ai_status = 'Evidence only' then
      raise exception 'An "Evidence only" finding has no AI status to confirm; record the officer''s decision as an override with a reason';
    end if;
    if not v_f.is_valid then
      raise exception 'An invalid finding (unverified quote or error) cannot be confirmed; override it with a reason';
    end if;
    if new.final_status <> v_f.ai_status then
      raise exception 'confirm must keep the AI status (%); use override to change it', v_f.ai_status;
    end if;
  end if;

  new.reviewed_at := clock_timestamp();
  return new;
end;
$$;

create trigger guard_officer_review
  before insert or update on public.officer_reviews
  for each row execute function private.guard_officer_review();

-- ---------------------------------------------------------------------------
-- Sign-off: every rule in the pack has a finding in the latest complete run,
-- and every such finding's latest review is a decision (confirm/override).
-- ---------------------------------------------------------------------------

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

  select count(*) into v_undecided
  from public.findings f
  left join lateral (
    select o.action from public.officer_reviews o
    where o.finding_id = f.id
    order by o.seq desc
    limit 1
  ) latest on true
  where f.run_id = v_run
    and (latest.action is null or latest.action = 'ask_applicant');

  return v_missing + v_undecided;
end;
$$;

grant execute on function private.unreviewed_findings(uuid) to authenticated, service_role;

create or replace function private.guard_sign_off()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_app        record;
  v_unreviewed integer;
begin
  if tg_op <> 'INSERT' then
    raise exception 'Sign-offs are final and cannot be changed';
  end if;

  select a.status, a.manual_assessment_requested into v_app
  from public.applications a where a.id = new.application_id;

  if v_app.status = 'draft' then
    raise exception 'A draft application cannot be signed off';
  end if;
  if exists (
    select 1 from public.assessment_runs r
    where r.application_id = new.application_id and r.status = 'running'
  ) then
    raise exception 'An assessment run is still in progress';
  end if;

  v_unreviewed := private.unreviewed_findings(new.application_id);
  if v_unreviewed is null then
    -- No AI run: only allowed where the applicant asked for manual assessment.
    if not v_app.manual_assessment_requested then
      raise exception 'SIGNOFF_BLOCKED: no complete assessment run to sign off';
    end if;
  elsif v_unreviewed > 0 then
    raise exception 'SIGNOFF_BLOCKED: % finding(s) have no officer decision', v_unreviewed;
  end if;

  new.signed_at := now();
  return new;
end;
$$;

create trigger guard_sign_off
  before insert or update on public.sign_offs
  for each row execute function private.guard_sign_off();

create or replace function private.after_sign_off()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  update public.applications set status = 'signed_off' where id = new.application_id;
  return new;
end;
$$;

create trigger after_sign_off
  after insert on public.sign_offs
  for each row execute function private.after_sign_off();

-- ---------------------------------------------------------------------------
-- Letters: an approved letter is frozen; changes need a new version.
-- Clarification requests: a sent request is frozen.
-- ---------------------------------------------------------------------------

create or replace function private.guard_letter()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if old.status = 'approved' then
    raise exception 'Letter version % is approved and cannot be changed; create a new version', old.version;
  end if;
  if new.application_id <> old.application_id or new.version <> old.version
     or new.source_finding_ids is distinct from old.source_finding_ids then
    raise exception 'Letter identity fields are immutable';
  end if;
  return new;
end;
$$;

create trigger guard_letter
  before update on public.letters
  for each row execute function private.guard_letter();

create or replace function private.guard_clarification()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
begin
  if old.status = 'sent' then
    raise exception 'A sent clarification request cannot be changed';
  end if;
  return new;
end;
$$;

create trigger guard_clarification
  before update on public.clarification_requests
  for each row execute function private.guard_clarification();

-- ---------------------------------------------------------------------------
-- Data retention for LLM inputs/outputs. The backend calls this with the
-- configured LLM_RETENTION_DAYS (null = only honour expires_at).
-- ---------------------------------------------------------------------------

create or replace function public.purge_expired_llm_data(p_retention_days integer default null)
returns integer
language plpgsql security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  delete from public.llm_cache c
  where (c.expires_at is not null and c.expires_at < now())
     or (p_retention_days is not null and c.created_at < now() - make_interval(days => p_retention_days));
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.purge_expired_llm_data(integer) from public, anon, authenticated;
grant execute on function public.purge_expired_llm_data(integer) to service_role;
