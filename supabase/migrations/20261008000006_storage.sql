-- =============================================================================
-- 0006 Storage: private bucket for SAMPLE documents.
--
-- Object path convention: {application_id}/{file_name}
-- Synthetic sample documents only - never real identity documents.
-- =============================================================================

insert into storage.buckets (id, name, public)
values ('application-documents', 'application-documents', false)
on conflict (id) do nothing;

create policy "documents: applicant reads own"
  on storage.objects for select to authenticated
  using (
    bucket_id = 'application-documents'
    and private.is_applicant_owner(private.try_uuid((storage.foldername(name))[1]))
  );

create policy "documents: applicant uploads to own draft"
  on storage.objects for insert to authenticated
  with check (
    bucket_id = 'application-documents'
    and private.is_applicant_owner(private.try_uuid((storage.foldername(name))[1]))
    and private.application_status(private.try_uuid((storage.foldername(name))[1])) = 'draft'
  );

create policy "documents: applicant deletes from own draft"
  on storage.objects for delete to authenticated
  using (
    bucket_id = 'application-documents'
    and private.is_applicant_owner(private.try_uuid((storage.foldername(name))[1]))
    and private.application_status(private.try_uuid((storage.foldername(name))[1])) = 'draft'
  );

create policy "documents: staff read"
  on storage.objects for select to authenticated
  using (
    bucket_id = 'application-documents'
    and private.is_staff()
    and private.can_staff_access(private.try_uuid((storage.foldername(name))[1]))
  );
