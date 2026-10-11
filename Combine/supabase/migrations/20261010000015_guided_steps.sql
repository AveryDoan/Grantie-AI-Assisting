-- =============================================================================
-- 0015 Guided steps: Documents, Redaction check, Assessment, Outcome.
--
-- - application_steps    where the officer is in the flow (steps 1 and 2 are recorded; 3 and 4 follow from the data)
-- - document_reviews     the officer's decision for each document slot (confirmed, wrong slot, request again, not needed + reason)
-- - redaction_edits      values the officer added as missed or unmasked as not personal. Values are stored ENCRYPTED
--                        (the same key as the token map), never in plain text.
-- - clarification_requests: a request to the applicant for more documents (drafted, edited and approved by an officer
--                        before it is sent; never sent automatically), and when the applicant replied
-- - documents.replaces_id / superseded: a re-uploaded file points at the one it replaces, so they can be compared
-- - letters.kind         decline letter or next-steps letter
-- - sign_off_history     a sign-off that was reopened (with the reason), so a new sign-off is a new version
-- Nothing here scores, ranks or decides anything.
-- =============================================================================

create table public.application_steps (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  step            smallint not null check (step between 1 and 4),
  status          text not null check (status in ('not_started', 'in_progress', 'done', 'waiting')),
  notice          text,
  detail          jsonb not null default '{}'::jsonb,
  updated_by      uuid references auth.users (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (application_id, step)
);

create table public.document_reviews (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  slot            text not null,
  document_id     uuid references public.documents (id) on delete set null,
  decision        text not null check (decision in ('confirmed', 'wrong_slot', 'request_again', 'not_needed')),
  reason          text,
  officer_id      uuid not null references auth.users (id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (application_id, slot),
  constraint not_needed_has_reason check (decision <> 'not_needed' or length(btrim(coalesce(reason, ''))) > 0)
);

create table public.redaction_edits (
  id               uuid primary key default gen_random_uuid(),
  application_id   uuid not null references public.applications (id) on delete cascade,
  kind             text not null check (kind in ('add', 'unmask')),
  token_type       text not null,
  source           text not null,
  encrypted_value  text not null,
  reason           text,
  officer_id       uuid not null references auth.users (id),
  created_at       timestamptz not null default now()
);

alter table public.clarification_requests
  add column kind text not null default 'finding' check (kind in ('finding', 'documents')),
  add column items jsonb not null default '[]'::jsonb,
  add column approved_by uuid references auth.users (id),
  add column approved_at timestamptz,
  add column resubmitted_at timestamptz;

alter table public.documents
  add column replaces_id uuid references public.documents (id) on delete set null,
  add column superseded boolean not null default false;

alter table public.letters
  add column kind text not null default 'decline' check (kind in ('decline', 'next_steps'));

create table public.sign_off_history (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  version         integer not null,
  officer_id      uuid not null references auth.users (id),
  signed_at       timestamptz not null,
  reopened_by     uuid not null references auth.users (id),
  reopened_at     timestamptz not null default now(),
  reason          text not null check (length(btrim(reason)) > 0),
  unique (application_id, version)
);

create trigger set_updated_at before update on public.application_steps for each row execute function private.set_updated_at();
create trigger set_updated_at before update on public.document_reviews for each row execute function private.set_updated_at();

alter table public.application_steps enable row level security;
alter table public.document_reviews enable row level security;
alter table public.redaction_edits enable row level security;
alter table public.sign_off_history enable row level security;
revoke all on public.application_steps, public.document_reviews, public.redaction_edits, public.sign_off_history from anon;
revoke insert, update, delete, truncate on public.application_steps, public.document_reviews, public.redaction_edits, public.sign_off_history from authenticated;

create policy application_steps_staff_select on public.application_steps
  for select to authenticated using (private.is_staff() and private.can_staff_access(application_id));
create policy document_reviews_staff_select on public.document_reviews
  for select to authenticated using (private.is_staff() and private.can_staff_access(application_id));
create policy sign_off_history_staff_select on public.sign_off_history
  for select to authenticated using (private.is_staff() and private.can_staff_access(application_id));
-- redaction_edits hold encrypted personal values: no direct read at all (the API uses the service role).
