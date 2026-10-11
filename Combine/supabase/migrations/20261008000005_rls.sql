-- =============================================================================
-- 0005 Row Level Security.
--
-- Roles (public.profiles.role):
--   officer   - reads/works applications of grant programs in their organisation
--   admin     - manages programs, rule packs, register, answer key; reads all
--   applicant - own draft application + documents; own letter once approved
--
-- The service role bypasses RLS (Supabase default); the backend uses it for
-- pipeline writes and audit inserts, and the integrity triggers still apply.
-- Anonymous users get nothing. No policy grants DELETE on audit data.
--
-- Deliberate tightening vs. "officers read/write findings": officers can read
-- findings but never edit them. Their decisions are officer_reviews rows,
-- so the AI suggestion and the human decision are both kept.
-- =============================================================================

-- Anonymous access: none, on every table.
revoke all on all tables in schema public from anon;
alter default privileges in schema public revoke all on tables from anon;

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    -- Not FORCEd: SECURITY DEFINER helpers run as the owner and must see all rows.
    execute format('alter table public.%I enable row level security', t);
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- organisations / profiles
-- ---------------------------------------------------------------------------

create policy organisations_select on public.organisations
  for select to authenticated
  using (private.is_admin() or id = private.user_org());

create policy organisations_admin_write on public.organisations
  for all to authenticated
  using (private.is_admin()) with check (private.is_admin());

create policy profiles_select on public.profiles
  for select to authenticated
  using (
    id = auth.uid()
    or private.is_admin()
    or (private.is_staff() and organisation_id = private.user_org())
  );

-- Only admins change roles / organisations. Users cannot promote themselves.
create policy profiles_admin_update on public.profiles
  for update to authenticated
  using (private.is_admin()) with check (private.is_admin());

-- ---------------------------------------------------------------------------
-- grant_programs / rule_packs / rules (published rules are readable)
-- ---------------------------------------------------------------------------

create policy grant_programs_select on public.grant_programs
  for select to authenticated
  using (active or private.can_staff_access_program(id));

create policy grant_programs_admin_write on public.grant_programs
  for all to authenticated
  using (private.is_admin()) with check (private.is_admin());

create policy rule_packs_select on public.rule_packs
  for select to authenticated
  using (status = 'approved' or private.can_staff_access_program(grant_program_id));

create policy rule_packs_admin_write on public.rule_packs
  for all to authenticated
  using (private.is_admin()) with check (private.is_admin());

create policy rules_select on public.rules
  for select to authenticated
  using (exists (
    select 1 from public.rule_packs rp
    where rp.id = rule_pack_id
      and (rp.status = 'approved' or private.can_staff_access_program(rp.grant_program_id))
  ));

create policy rules_admin_write on public.rules
  for all to authenticated
  using (private.is_admin()) with check (private.is_admin());

-- ---------------------------------------------------------------------------
-- applicants
-- ---------------------------------------------------------------------------

create policy applicants_select on public.applicants
  for select to authenticated
  using (user_id = auth.uid() or private.staff_can_see_applicant(id));

create policy applicants_insert_own on public.applicants
  for insert to authenticated
  with check (user_id = auth.uid() and private.user_role() = 'applicant');

create policy applicants_update_own on public.applicants
  for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- applications
-- Applicants: read their own applications (so they can see progress), but
-- write only while status = draft. Staff: their organisation's programs.
-- ---------------------------------------------------------------------------

create policy applications_select on public.applications
  for select to authenticated
  using (
    private.owns_applicant(applicant_id)
    or private.can_staff_access_program(grant_program_id)
  );

create policy applications_applicant_insert on public.applications
  for insert to authenticated
  with check (
    private.user_role() = 'applicant'
    and private.owns_applicant(applicant_id)
    and status = 'draft'
  );

create policy applications_applicant_update on public.applications
  for update to authenticated
  using (private.user_role() = 'applicant' and private.owns_applicant(applicant_id) and status = 'draft')
  with check (private.owns_applicant(applicant_id) and status in ('draft', 'submitted'));

create policy applications_applicant_delete on public.applications
  for delete to authenticated
  using (private.user_role() = 'applicant' and private.owns_applicant(applicant_id) and status = 'draft');

create policy applications_staff_update on public.applications
  for update to authenticated
  using (private.is_staff() and private.can_staff_access_program(grant_program_id))
  with check (private.can_staff_access_program(grant_program_id));

-- ---------------------------------------------------------------------------
-- redaction_maps: staff read only (contains the identifiers the LLM never sees)
-- ---------------------------------------------------------------------------

create policy redaction_maps_staff_select on public.redaction_maps
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

-- ---------------------------------------------------------------------------
-- documents
-- ---------------------------------------------------------------------------

create policy documents_select on public.documents
  for select to authenticated
  using (
    private.is_applicant_owner(application_id)
    or (private.is_staff() and private.can_staff_access(application_id))
  );

create policy documents_applicant_insert on public.documents
  for insert to authenticated
  with check (private.is_applicant_owner(application_id)
              and private.application_status(application_id) = 'draft');

create policy documents_applicant_update on public.documents
  for update to authenticated
  using (private.is_applicant_owner(application_id)
         and private.application_status(application_id) = 'draft')
  with check (private.is_applicant_owner(application_id)
              and private.application_status(application_id) = 'draft');

create policy documents_applicant_delete on public.documents
  for delete to authenticated
  using (private.is_applicant_owner(application_id)
         and private.application_status(application_id) = 'draft');

-- ---------------------------------------------------------------------------
-- AI outputs: staff read only. Applicants never see findings.
-- Writes come from the backend (service role).
-- ---------------------------------------------------------------------------

create policy assessment_runs_staff_select on public.assessment_runs
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy fact_extractions_staff_select on public.fact_extractions
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy findings_staff_select on public.findings
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

-- ---------------------------------------------------------------------------
-- officer_reviews: officers record their own decisions (append-only).
-- ---------------------------------------------------------------------------

create policy officer_reviews_staff_select on public.officer_reviews
  for select to authenticated
  using (exists (
    select 1 from public.findings f
    where f.id = finding_id and private.is_staff() and private.can_staff_access(f.application_id)
  ));

create policy officer_reviews_officer_insert on public.officer_reviews
  for insert to authenticated
  with check (
    officer_id = auth.uid()
    and private.user_role() = 'officer'
    and exists (
      select 1 from public.findings f
      where f.id = finding_id and private.can_staff_access(f.application_id)
    )
  );

-- ---------------------------------------------------------------------------
-- clarification_requests
-- Applicants see a request only once it has been (mock) sent.
-- ---------------------------------------------------------------------------

create policy clarification_select on public.clarification_requests
  for select to authenticated
  using (
    (private.is_staff() and private.can_staff_access(application_id))
    or (status = 'sent' and private.is_applicant_owner(application_id))
  );

create policy clarification_staff_insert on public.clarification_requests
  for insert to authenticated
  with check (
    created_by = auth.uid()
    and private.user_role() = 'officer'
    and private.can_staff_access(application_id)
  );

create policy clarification_staff_update on public.clarification_requests
  for update to authenticated
  using (private.user_role() = 'officer' and private.can_staff_access(application_id))
  with check (private.can_staff_access(application_id));

-- ---------------------------------------------------------------------------
-- letters: applicants read only their own APPROVED letter.
-- ---------------------------------------------------------------------------

create policy letters_select on public.letters
  for select to authenticated
  using (
    (private.is_staff() and private.can_staff_access(application_id))
    or (status = 'approved' and private.is_applicant_owner(application_id))
  );

create policy letters_staff_insert on public.letters
  for insert to authenticated
  with check (private.user_role() = 'officer' and private.can_staff_access(application_id)
              and status = 'draft');

create policy letters_staff_update on public.letters
  for update to authenticated
  using (private.user_role() = 'officer' and private.can_staff_access(application_id))
  with check (
    private.can_staff_access(application_id)
    and (status <> 'approved' or approved_by = auth.uid())
  );

-- ---------------------------------------------------------------------------
-- sign_offs
-- ---------------------------------------------------------------------------

create policy sign_offs_staff_select on public.sign_offs
  for select to authenticated
  using (private.is_staff() and private.can_staff_access(application_id));

create policy sign_offs_officer_insert on public.sign_offs
  for insert to authenticated
  with check (
    officer_id = auth.uid()
    and private.user_role() = 'officer'
    and private.can_staff_access(application_id)
  );

-- ---------------------------------------------------------------------------
-- audit_log: staff read; inserts only via service role (no insert policy and
-- no insert privilege for authenticated). No update/delete for anyone.
-- ---------------------------------------------------------------------------

create policy audit_log_staff_select on public.audit_log
  for select to authenticated
  using (
    private.is_admin()
    or (private.user_role() = 'officer'
        and application_id is not null
        and private.can_staff_access(application_id))
  );

-- ---------------------------------------------------------------------------
-- mock register and evaluation: staff read, admin write.
-- ---------------------------------------------------------------------------

create policy register_staff_select on public.mock_grants_register
  for select to authenticated using (private.is_staff());
create policy register_admin_write on public.mock_grants_register
  for all to authenticated using (private.is_admin()) with check (private.is_admin());

create policy evaluation_cases_staff_select on public.evaluation_cases
  for select to authenticated using (private.is_staff());
create policy evaluation_cases_admin_write on public.evaluation_cases
  for all to authenticated using (private.is_admin()) with check (private.is_admin());

create policy answer_key_staff_select on public.answer_key
  for select to authenticated using (private.is_staff());
create policy answer_key_admin_write on public.answer_key
  for all to authenticated using (private.is_admin()) with check (private.is_admin());

create policy evaluation_runs_staff_select on public.evaluation_runs
  for select to authenticated using (private.is_staff());

create policy evaluation_results_staff_select on public.evaluation_results
  for select to authenticated using (private.is_staff());

-- llm_cache: RLS enabled with no policies -> service role only.
-- (Intentionally no policies here.)
