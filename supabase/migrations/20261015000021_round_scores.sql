-- =============================================================================
-- 0021 Weighted total per applicant (Outcomes page only) and who may see / record it.
--
-- The total is plain arithmetic on the officer's own marks and the weights in the approved rule pack:
--     sum(mark * weight) / sum(weights)
-- Nothing else feeds it: no AI output, no finding, no consistency or AI-use signal. It is shown only on the Outcomes page, to
-- people with can_view_round_scores. It is never in a letter, an email, the Overview or an AI prompt.
-- The API uses this function (through the same arithmetic in app/services/scores.py); a test keeps the two equal.
-- not_assessed_policy (rule pack letter_config): 'block' (default: no total), 'zero' or 'reweight'.
-- =============================================================================

alter table public.profiles add column permissions text[];     -- null = not restricted yet; otherwise the listed permissions only
alter table public.round_decisions add column score_snapshot jsonb;   -- marks, weights, rule pack version, total, time (never rewritten)

-- Returns the unrounded total, or null when it cannot be calculated (weights missing / not positive, a mark missing, or a
-- Not assessed criterion under the 'block' policy).
create or replace function private.weighted_total(p_application uuid)
returns numeric
language plpgsql stable security definer
set search_path = ''
as $$
declare
  v_pack   uuid;
  v_policy text;
  v_sum    numeric := 0;
  v_points numeric := 0;
  r        record;
begin
  select a.rule_pack_id into v_pack from public.applications a where a.id = p_application;
  select coalesce(p.letter_config ->> 'not_assessed_policy', 'block') into v_policy from public.rule_packs p where p.id = v_pack;
  -- a weight that is present must be a positive number; a criterion with no weight is marked but not scored
  if exists (select 1 from public.rules ru where ru.rule_pack_id = v_pack and ru.params ->> 'section' = 'merit'
               and ru.params ->> 'weight' is not null and (ru.params ->> 'weight')::numeric <= 0) then
    return null;
  end if;
  for r in
    select (ru.params ->> 'weight')::numeric as w, ru.params ->> 'weight' is null as unweighted, m.mark, m.not_assessed, m.id as mark_id
    from public.rules ru
    left join public.merit_marks m on m.application_id = p_application and m.rule_code = ru.rule_code
    where ru.rule_pack_id = v_pack and ru.params ->> 'section' = 'merit'
  loop
    if r.mark_id is null then
      return null;                                   -- not marked yet
    elsif r.unweighted then
      null;                                          -- marked, never scored
    elsif r.not_assessed then
      if v_policy = 'block' then return null;
      elsif v_policy = 'zero' then v_sum := v_sum + r.w;                        -- counts as 0
      end if;                                                                   -- 'reweight': left out
    else
      v_sum := v_sum + r.w;
      v_points := v_points + r.mark * r.w;
    end if;
  end loop;
  if v_sum = 0 then return null; end if;
  return v_points / v_sum;
end;
$$;
grant execute on function private.weighted_total(uuid) to authenticated, service_role;

-- One row per application: the unrounded total (null = cannot calculate yet). Staff of the same organisation only.
create or replace view public.application_scores with (security_invoker = true) as
  select a.id as application_id, private.weighted_total(a.id) as weighted_total
  from public.applications a
  where private.is_staff() and private.can_staff_access(a.id);
revoke all on public.application_scores from anon;
