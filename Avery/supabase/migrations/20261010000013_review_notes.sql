-- =============================================================================
-- 0013 Review screen: why a document needs attention, and AI summaries linked to their source passages.
--
-- - documents.attention_level / attention_reason  worked out by code at assessment time, stored with the document:
--     ok | check | attention, and one plain sentence ("Name on the CoE differs from the form").
--     A reason to look, never a verdict. Nothing here changes a rule result.
-- - findings.ai_summaries  for judgement criteria: short AI paraphrases, each tied to the passages it came from.
--     A summary whose passages code cannot find in the text is stored as unlinked and shown separately.
--     There is no score, rating or strength field.
-- =============================================================================

alter table public.documents
  add column attention_level text check (attention_level in ('ok', 'check', 'attention')),
  add column attention_reason text;

alter table public.findings
  add column ai_summaries jsonb not null default '[]'::jsonb;
alter table public.findings
  add constraint findings_ai_summaries_is_array check (jsonb_typeof(ai_summaries) = 'array');
