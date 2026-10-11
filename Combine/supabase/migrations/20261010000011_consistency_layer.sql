-- =============================================================================
-- 0011 Consistency layer: "the story doesn't add up" signals.
--
-- Signals, never verdicts. A flag points to two pieces of evidence that do not
-- fit together and asks an officer to check. It is not a score, a risk rating or
-- a recommendation, it never changes a rule result, and it never blocks
-- sign-off. Each flag is confirmed or dismissed by an officer (dismissal needs a
-- note), and every decision is audited by the API.
--
-- - consistency_flags       the flags and the officer's decision on each
-- - identifier_hashes       salted keyed hashes (HMAC, key in the API's environment)
--                           of contact and referee identifiers, so applications
--                           can be linked WITHOUT storing the identifiers
-- - document_fingerprints   keyed hashes of word shingles of the REDACTED text,
--                           to spot reused document text
-- - documents.integrity_signals  signals derived from PDF metadata at upload
--                           (dates, a closed-list software class, booleans).
--                           Raw author and producer strings are never stored.
-- - assessment_runs.consistency_trace  what the LLM checks were given and what
--                           they returned, with the code's quote verification
--
-- Nothing here is readable by applicants. Writes are service-role only.
-- =============================================================================

alter table public.documents
  add column integrity_signals jsonb not null default '{}'::jsonb;
comment on column public.documents.integrity_signals is
  'Derived from PDF metadata before it is stripped: dates, producer class, booleans. Never raw author or producer text.';

alter table public.assessment_runs
  add column consistency_trace jsonb not null default '{}'::jsonb;

create table public.consistency_flags (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  run_id          uuid references public.assessment_runs (id) on delete set null,
  -- Deterministic: the same inconsistency found again keeps the same row (and the officer's decision).
  flag_key        text not null,
  check_id        text not null,
  check_type      text not null
                    check (check_type in ('cross_document', 'timeline', 'document_integrity', 'cross_application', 'narrative')),
  strength        text not null check (strength in ('strong', 'weak')),
  description     text not null,
  -- [{kind, source, label, quote, verified, field, value}] - quotes come from REDACTED text.
  evidence        jsonb not null default '[]'::jsonb,
  -- 'unclear': an AI-reported item whose quotes the code could not verify. Shown as unclear, never as a finding.
  verification    text not null default 'verified' check (verification in ('verified', 'unclear')),
  status          text not null default 'open' check (status in ('open', 'confirmed', 'dismissed')),
  note            text,
  reviewed_by     uuid references auth.users (id),
  reviewed_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (application_id, flag_key),
  -- Document metadata and layout are weak signals, always.
  constraint integrity_is_weak check (check_type <> 'document_integrity' or strength = 'weak'),
  constraint dismissal_needs_note check (status <> 'dismissed' or length(btrim(coalesce(note, ''))) > 0),
  constraint decision_recorded check (status = 'open' or (reviewed_by is not null and reviewed_at is not null)),
  -- The system points to inconsistencies; it never accuses.
  constraint neutral_wording check (description !~* '(fraud|fake|guilty|suspicious)')
);
comment on table public.consistency_flags is
  'An inconsistency for an officer to check. Never a verdict, score or recommendation. Synthetic data only until a PIA.';

create table public.identifier_hashes (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  kind            text not null
                    check (kind in ('contact_email', 'contact_phone', 'contact_address', 'referee_name',
                                    'referee_email', 'referee_email_domain', 'referee_phone')),
  hash            text not null check (hash ~ '^[0-9a-f]{64}$'),
  source          text not null default 'form',   -- 'form' or 'document:<id>': where it was found, never the value
  created_at      timestamptz not null default now()
);
comment on table public.identifier_hashes is
  'HMAC-SHA256 of a normalised identifier, keyed by IDENTIFIER_HASH_KEY. The identifier itself is never stored or logged.';

create table public.document_fingerprints (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references public.applications (id) on delete cascade,
  scope           text not null,                 -- 'document:<id>' or 'answers' (the written answers)
  document_id     uuid references public.documents (id) on delete cascade,
  doc_type        text not null,
  label           text not null,                 -- plain label such as 'Referee letter 1'
  shingle_count   integer not null,
  sketch          jsonb not null,                -- the smallest keyed shingle hashes (a bottom-k sketch)
  created_at      timestamptz not null default now(),
  unique (application_id, scope)
);

create index on public.consistency_flags (application_id, status);
create index on public.consistency_flags (check_type, strength);
create index on public.identifier_hashes (kind, hash);
create index on public.identifier_hashes (application_id);
create index on public.document_fingerprints (application_id);

create trigger set_updated_at before update on public.consistency_flags
  for each row execute function private.set_updated_at();

alter table public.consistency_flags enable row level security;
alter table public.identifier_hashes enable row level security;
alter table public.document_fingerprints enable row level security;

revoke all on public.consistency_flags, public.identifier_hashes, public.document_fingerprints from anon;
revoke insert, update, delete, truncate
  on public.consistency_flags, public.identifier_hashes, public.document_fingerprints from authenticated;

-- Officers and admins of the application's organisation may read. Applicants never can.
create policy consistency_flags_staff_select on public.consistency_flags
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy identifier_hashes_staff_select on public.identifier_hashes
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy document_fingerprints_staff_select on public.document_fingerprints
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));
