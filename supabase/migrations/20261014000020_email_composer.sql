-- =============================================================================
-- 0020 Email composer: a request to the applicant now keeps its subject, recipient, due date and how it was delivered.
-- Nothing is sent by the database or the API: "mark as sent" records that the officer sent it themselves, "send" is only
-- available once an email provider is connected. The text that was sent is kept (it is also in the audit log).
-- =============================================================================
alter table public.clarification_requests
  add column subject        text,
  add column to_email       text,
  add column cc_email       text,
  add column due_date       date,
  add column delivery       text check (delivery in ('sent', 'marked_sent', 'copied')),
  add column body_template  boolean not null default false;   -- built from a fixed template (the AI was not used)
