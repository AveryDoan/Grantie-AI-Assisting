-- =============================================================================
-- 0018 AI-use check (optional weak signal) and the applicant's acknowledgement of the form's AI-use rule.
--
-- The check runs a local detector model over the applicant's free-text answers, only when an officer asks, and only when the
-- rule pack's fairness gate switched it on. Its result never changes a rule status, a merit mark, a letter or the outcome.
-- ai_use_checks is the trace (model, revision, thresholds, raw scores per field). ai_use_actions holds the officer's action per
-- field. Both are append-only for staff: no update or delete is granted. Every write is also in the audit log.
-- =============================================================================

alter table public.applications
  add column ai_use_acknowledged    boolean not null default false,   -- "I have followed these rules."
  add column ai_use_acknowledged_at timestamptz;

create table public.ai_use_checks (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  model           text not null,
  revision        text not null,
  thresholds      jsonb not null,
  min_words       integer not null,
  fields          jsonb not null,           -- [{field, words, result, windows, scores, mean}]: raw scores stay here, never on screen
  run_by          uuid references auth.users (id),
  created_at      timestamptz not null default now()
);

create table public.ai_use_actions (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  check_id        uuid not null references public.ai_use_checks (id) on delete cascade,
  field           text not null,
  action          text not null check (action in ('no_action', 'note', 'ask')),
  note            text,
  request_id      uuid references public.clarification_requests (id),
  officer_id      uuid references auth.users (id),
  created_at      timestamptz not null default now()
);

do $$
declare t text;
begin
  foreach t in array array['ai_use_checks', 'ai_use_actions'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke insert, update, delete, truncate on public.%I from authenticated', t);
    execute format('create policy %I on public.%I for select to authenticated using (private.is_staff() and private.can_staff_access(application_id))',
                   t || '_staff_select', t);
  end loop;
end $$;

comment on table public.ai_use_checks is
  'AI-use check runs: a weak signal for the officer from a local detector. Never a finding, a score or a reason for an outcome.';
