-- Behaviour checks for schema, triggers and RLS (synthetic fixtures only).
-- Run on a scratch DB: local_supabase_stub.sql, then all migrations, then this file.
\set ON_ERROR_STOP 1
-- ---------- assertion helpers (schema `checks`, test-only) ----------
create schema if not exists checks;
grant usage on schema checks to authenticated, anon, service_role;
create or replace function checks.expect_error(p_sql text, p_pattern text, p_label text) returns void language plpgsql as $$
begin
  begin execute p_sql;
  exception when others then
    if sqlerrm ~* p_pattern then raise notice 'PASS  %', p_label; return; end if;
    raise exception 'FAIL  % : wrong error: %', p_label, sqlerrm;
  end;
  raise exception 'FAIL  % : statement succeeded', p_label;
end $$;
create or replace function checks.expect_count(p_sql text, p_n int, p_label text) returns void language plpgsql as $$
declare n int; begin execute 'with x as (' || p_sql || ') select count(*) from x' into n;
  if n = p_n then raise notice 'PASS  % (%)', p_label, n; else raise exception 'FAIL  % : got % expected %', p_label, n, p_n; end if; end $$;
create or replace function checks.as_user(p uuid) returns void language plpgsql as $$
begin perform set_config('request.jwt.claims', json_build_object('sub',p,'role','authenticated')::text, true);
  execute 'set local role authenticated'; end $$;
grant execute on all functions in schema checks to authenticated, anon, service_role;

-- ---------- fixtures (as postgres) ----------
insert into auth.users (id,email) values
 ('00000000-0000-0000-0000-0000000000a1','officerA@example.test'),
 ('00000000-0000-0000-0000-0000000000b1','officerB@example.test'),
 ('00000000-0000-0000-0000-0000000000ad','admin@example.test'),
 ('00000000-0000-0000-0000-0000000000c1','app1@example.test'),
 ('00000000-0000-0000-0000-0000000000c2','app2@example.test');
insert into organisations (id,name) values ('10000000-0000-0000-0000-00000000000a','Org A'),('10000000-0000-0000-0000-00000000000b','Org B');
update profiles set role='officer', organisation_id='10000000-0000-0000-0000-00000000000a' where id='00000000-0000-0000-0000-0000000000a1';
update profiles set role='officer', organisation_id='10000000-0000-0000-0000-00000000000b' where id='00000000-0000-0000-0000-0000000000b1';
update profiles set role='admin' where id='00000000-0000-0000-0000-0000000000ad';
insert into grant_programs (id,organisation_id,name) values ('20000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-00000000000a','Test program');
insert into rule_packs (id,grant_program_id,version) values ('30000000-0000-0000-0000-000000000001','20000000-0000-0000-0000-000000000001','v1');
insert into rules (id,rule_pack_id,rule_code,rule_text,rule_type,check_method) values
 ('40000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','R1','Is not-for-profit','factual','llm'),
 ('40000000-0000-0000-0000-000000000002','30000000-0000-0000-0000-000000000001','R2','Community benefit','judgement','llm'),
 ('40000000-0000-0000-0000-000000000003','30000000-0000-0000-0000-000000000001','R3','Max 2 active grants','cross_application','code');
select checks.expect_error($$insert into applications (grant_program_id,rule_pack_id,applicant_id) values ('20000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001',gen_random_uuid())$$,'approved rule pack','application refused on draft pack');
update rule_packs set status='approved', approved_by='00000000-0000-0000-0000-0000000000ad', approved_at=now() where id='30000000-0000-0000-0000-000000000001';
select checks.expect_error($$insert into rules (rule_pack_id,rule_code,rule_text,rule_type,check_method) values ('30000000-0000-0000-0000-000000000001','R9','x','factual','llm')$$,'approved rule pack','rules frozen once pack approved');
select checks.expect_error($$update rule_packs set source_notes='edited' where id='30000000-0000-0000-0000-000000000001'$$,'can only be retired','approved pack content frozen');
select checks.expect_error($$insert into rules (rule_pack_id,rule_code,rule_text,rule_type,check_method) values ('30000000-0000-0000-0000-000000000001','R8','x','judgement','code')$$,'judgement_not_code|approved','judgement cannot be code');

insert into applicants (id,user_id,display_name) values
 ('50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000c1','Fictional Applicant One'),
 ('50000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000c2','Fictional Applicant Two');
insert into applications (id,grant_program_id,rule_pack_id,applicant_id,status,submitted_at,application_text) values
 ('60000000-0000-0000-0000-000000000001','20000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000001','submitted',now(),'{"q":"We are a not-for-profit association."}'),
 ('60000000-0000-0000-0000-000000000002','20000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000002','draft',null,'{}'),
 ('60000000-0000-0000-0000-000000000003','20000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000002','submitted',now(),'{}');

-- ---------- manual assessment opt-out ----------
update applications set manual_assessment_requested=true where id='60000000-0000-0000-0000-000000000003';
select checks.expect_error($$insert into assessment_runs (application_id,rule_pack_id,model_name,prompt_version) values ('60000000-0000-0000-0000-000000000003','30000000-0000-0000-0000-000000000001','m','p1')$$,'MANUAL_ASSESSMENT_REQUESTED','pipeline refuses when manual assessment requested');
select checks.expect_error($$update applications set manual_assessment_requested=false where id='60000000-0000-0000-0000-000000000003'$$,'cannot be withdrawn','opt-out cannot be reversed (even by service)');
select checks.expect_error($$insert into assessment_runs (application_id,rule_pack_id,model_name,prompt_version) values ('60000000-0000-0000-0000-000000000002','30000000-0000-0000-0000-000000000001','m','p1')$$,'status draft','no assessment of drafts');

-- ---------- run + findings ----------
insert into assessment_runs (id,application_id,rule_pack_id,model_name,prompt_version) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','m','p1');
select checks.expect_count($$select 1 from assessment_runs where rule_pack_version='v1'$$,1,'run stamped with rule pack version');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,evidence_quote,quote_verified,is_valid) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','Met','llm','q',true,true)$$,'Evidence only','judgement rule cannot return a status');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,confidence) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','Evidence only','llm','high')$$,'evidence_only_no_confidence','evidence only carries no confidence');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Evidence only','llm')$$,'reserved','factual rule cannot be evidence-only');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,language_flag) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Not met','llm',true)$$,'language_flag_is_unclear','language obstacle cannot be Not met');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,error_flag) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Met','llm',true)$$,'error_is_unclear','LLM error cannot be Met');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,evidence_quote,quote_verified,is_valid) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Met','llm','fabricated',false,true)$$,'valid_requires_verified_quote','unverified quote cannot be valid');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,is_valid) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Met','llm',true)$$,'llm_decision_needs_quote','LLM Met without a quote cannot be valid');
select checks.expect_error($$insert into findings (run_id,application_id,rule_id,ai_status,check_source,evidence_quote,quote_verified,is_valid) values ('70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000003','Met','llm','x',true,true)$$,'does not match rule check_method','check_source must match rule');
insert into findings (id,run_id,application_id,rule_id,ai_status,check_source,evidence_quote,quote_verified,is_valid,confidence) values
 ('80000000-0000-0000-0000-000000000001','70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Met','llm','We are a not-for-profit association.',true,true,'high'),
 ('80000000-0000-0000-0000-000000000002','70000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','Evidence only','llm',null,false,true,null);
-- R3 deliberately missing for now
update assessment_runs set status='complete', finished_at=now() where id='70000000-0000-0000-0000-000000000001';
select checks.expect_error($$update assessment_runs set status='running' where id='70000000-0000-0000-0000-000000000001'$$,'cannot change status','finished run immutable');

-- ---------- audit log ----------
set role service_role;
insert into audit_log (actor_role,action,application_id) values ('system','test.insert','60000000-0000-0000-0000-000000000001');
select checks.expect_error($$update audit_log set reason='x'$$,'append-only|permission','service role cannot update audit_log');
select checks.expect_error($$delete from audit_log$$,'append-only|permission','service role cannot delete audit_log');
reset role;
select checks.expect_error($$update audit_log set reason='x'$$,'append-only','owner cannot update audit_log');
select checks.expect_error($$delete from audit_log$$,'append-only','owner cannot delete audit_log');
select checks.expect_error($$truncate audit_log$$,'append-only','owner cannot truncate audit_log');

-- ---------- RLS: applicant ----------
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c1');
select checks.expect_count('select * from findings',0,'applicant cannot read findings');
select checks.expect_count('select * from audit_log',0,'applicant cannot read audit_log');
select checks.expect_count('select * from applications',1,'applicant sees only own application');
select checks.expect_count('select * from applicants',1,'applicant sees only own applicant record');
select checks.expect_count('select * from redaction_maps',0,'applicant cannot read redaction map');
select checks.expect_count($$update applications set application_text='{"q":"changed"}' where id='60000000-0000-0000-0000-000000000001' returning 1$$,0,'applicant cannot edit a submitted application');
select checks.expect_error($$insert into audit_log (actor_role,action) values ('applicant','forged')$$,'permission denied','applicant cannot insert audit_log');
select checks.expect_count($$update profiles set role='admin' where id=auth.uid() returning 1$$,0,'applicant cannot promote self');
rollback;

begin; select checks.as_user('00000000-0000-0000-0000-0000000000c2');
select checks.expect_count($$update applications set application_text='{"q":"draft edit"}' where id='60000000-0000-0000-0000-000000000002' returning 1$$,1,'applicant can edit own draft');
select checks.expect_error($$update applications set status='in_review' where id='60000000-0000-0000-0000-000000000002'$$,'row-level security|only save a draft','applicant cannot move to in_review');
select checks.expect_error($$insert into assessment_runs (application_id,rule_pack_id,model_name,prompt_version) values ('60000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','m','p')$$,'row-level security','applicant cannot start a run');
rollback;

-- ---------- RLS: officer scoping ----------
begin; select checks.as_user('00000000-0000-0000-0000-0000000000b1');
select checks.expect_count('select * from applications',0,'officer in other org sees no applications');
select checks.expect_count('select * from findings',0,'officer in other org sees no findings');
select checks.expect_count('select * from audit_log',0,'officer in other org sees no audit rows');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
select checks.expect_count('select * from applications',3,'officer sees own org applications');
select checks.expect_count('select * from findings',2,'officer sees findings');
select checks.expect_count('select * from audit_log',1,'officer reads audit_log');
select checks.expect_count($$update findings set ai_status='Not met' where id='80000000-0000-0000-0000-000000000001' returning 1$$,0,'officer cannot edit AI findings');
select checks.expect_error($$update applications set application_text='{}' where id='60000000-0000-0000-0000-000000000001'$$,'cannot edit','officer cannot edit submission');
select checks.expect_error($$update applications set status='signed_off' where id='60000000-0000-0000-0000-000000000001'$$,'sign-off record','officer cannot set signed_off directly');
rollback;

-- ---------- officer reviews + sign-off ----------
begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000001',auth.uid(),'override','Not met')$$,'reason_required','override without reason rejected');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status,reason) values ('80000000-0000-0000-0000-000000000001',auth.uid(),'override','Not met','   ')$$,'reason_required','blank reason rejected');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000001',auth.uid(),'confirm','Not met')$$,'reason_required|keep the AI status','confirm to Not met rejected');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000002',auth.uid(),'confirm','Met')$$,'no AI status to confirm','cannot confirm Evidence only');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000b1','confirm','Met')$$,'row-level security','cannot review as another officer');
insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000001',auth.uid(),'confirm','Met');
select checks.expect_error($$insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001',auth.uid(),true)$$,'SIGNOFF_BLOCKED: 2','sign-off blocked: 1 unreviewed + 1 rule missing a finding');
insert into officer_reviews (finding_id,officer_id,action,final_status,reason) values ('80000000-0000-0000-0000-000000000002',auth.uid(),'override','Met','Strong evidence of local benefit in Q3 answer');
select checks.expect_error($$insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001',auth.uid(),true)$$,'SIGNOFF_BLOCKED: 1','sign-off blocked: rule with no finding');
commit;

-- add the missing R3 finding via a new run (old reviews no longer current)
insert into assessment_runs (id,application_id,rule_pack_id,model_name,prompt_version) values ('70000000-0000-0000-0000-000000000002','60000000-0000-0000-0000-000000000001','30000000-0000-0000-0000-000000000001','m','p1');
insert into findings (id,run_id,application_id,rule_id,ai_status,check_source,evidence_quote,quote_verified,is_valid,confidence) values
 ('80000000-0000-0000-0000-000000000011','70000000-0000-0000-0000-000000000002','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','Met','llm','We are a not-for-profit association.',true,true,'high'),
 ('80000000-0000-0000-0000-000000000012','70000000-0000-0000-0000-000000000002','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','Evidence only','llm',null,false,true,null),
 ('80000000-0000-0000-0000-000000000013','70000000-0000-0000-0000-000000000002','60000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000003','Unclear','code',null,false,false,'low');
select checks.expect_error($$insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1',true)$$,'in progress','sign-off blocked while run is running');
update assessment_runs set status='complete', finished_at=now() where id='70000000-0000-0000-0000-000000000002';

begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000001',auth.uid(),'confirm','Met')$$,'latest complete','old-run findings cannot be reviewed');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000013',auth.uid(),'confirm','Unclear')$$,'invalid finding','invalid finding cannot be confirmed');
insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000011',auth.uid(),'confirm','Met');
insert into officer_reviews (finding_id,officer_id,action,final_status,reason) values ('80000000-0000-0000-0000-000000000012',auth.uid(),'override','Met','Q3 describes weekly community events');
insert into officer_reviews (finding_id,officer_id,action) values ('80000000-0000-0000-0000-000000000013',auth.uid(),'ask_applicant');
select checks.expect_error($$insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001',auth.uid(),true)$$,'SIGNOFF_BLOCKED: 1','ask_applicant is not a decision');
insert into officer_reviews (finding_id,officer_id,action,final_status,reason) values ('80000000-0000-0000-0000-000000000013',auth.uid(),'override','Met','Register checked manually: 1 active grant');
select checks.expect_error($$insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001',auth.uid(),false)$$,'statement_must_be_acknowledged','statement must be acknowledged');
insert into sign_offs (application_id,officer_id,statement_acknowledged) values ('60000000-0000-0000-0000-000000000001',auth.uid(),true);
select checks.expect_count($$select 1 from applications where id='60000000-0000-0000-0000-000000000001' and status='signed_off'$$,1,'sign-off sets application status');
select checks.expect_error($$insert into officer_reviews (finding_id,officer_id,action,final_status) values ('80000000-0000-0000-0000-000000000011',auth.uid(),'confirm','Met')$$,'signed off','no reviews after sign-off');
select checks.expect_count($$update officer_reviews set reason='rewrite' returning 1$$,0,'officer cannot edit reviews (RLS)');
-- letters
insert into letters (id,application_id,version,body_text) values ('90000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001',1,'Draft');
commit;

select checks.expect_error($$update officer_reviews set reason='rewrite'$$,'append-only','reviews append-only even for owner');
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c1');
select checks.expect_count('select * from letters',0,'applicant cannot see draft letter');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
update letters set status='approved', approved_by=auth.uid(), approved_at=now() where id='90000000-0000-0000-0000-000000000001';
select checks.expect_error($$update letters set body_text='changed' where id='90000000-0000-0000-0000-000000000001'$$,'approved and cannot be changed','approved letter frozen');
commit;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c1');
select checks.expect_count('select * from letters',1,'applicant sees own approved letter');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c2');
select checks.expect_count('select * from letters',0,'other applicant cannot see the letter');
rollback;

-- ---------- anon + llm_cache + retention ----------
begin; set local role anon;
select checks.expect_error('select * from applications','permission denied','anon has no table access');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
select checks.expect_count('select * from llm_cache',0,'officer cannot read llm_cache');
select checks.expect_error('select public.purge_expired_llm_data(30)','permission denied','clients cannot call purge');
rollback;
insert into llm_cache (cache_key,provider,model_name,prompt_version,rule_pack_version,response,created_at) values ('old','gemini','m','p','v1','{}',now()-interval '40 days'),('new','gemini','m','p','v1','{}',now());
set role service_role;
select checks.expect_count('select public.purge_expired_llm_data(30) as n where public.purge_expired_llm_data(0) >= 0',1,'retention purge runs for service role');
reset role;

-- ---------- storage ----------
insert into storage.objects (bucket_id,name) values ('application-documents','60000000-0000-0000-0000-000000000002/coe.pdf');
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c2');
select checks.expect_count('select * from storage.objects',1,'applicant sees own object');
insert into storage.objects (bucket_id,name) values ('application-documents','60000000-0000-0000-0000-000000000002/visa.pdf');
select checks.expect_error($$insert into storage.objects (bucket_id,name) values ('application-documents','60000000-0000-0000-0000-000000000003/x.pdf')$$,'row-level security','applicant cannot upload to submitted application');
select checks.expect_error($$insert into storage.objects (bucket_id,name) values ('application-documents','not-a-uuid/x.pdf')$$,'row-level security','bad path rejected');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c1');
select checks.expect_count('select * from storage.objects',0,'applicant cannot see others objects');
rollback;

-- ---------- consistency layer (flags are signals for officers: never verdicts) ----------
insert into consistency_flags (id,application_id,flag_key,check_id,check_type,strength,description) values
 ('a0000000-0000-0000-0000-000000000001','60000000-0000-0000-0000-000000000001','k1','cross_document.arrival_vs_start','cross_document','strong','The arrival date is 28 days after the course starts.');
select checks.expect_error($$insert into consistency_flags (application_id,flag_key,check_id,check_type,strength,description) values ('60000000-0000-0000-0000-000000000001','k2','document_integrity.x','document_integrity','strong','A PDF signal')$$,'integrity_is_weak','document signals can never be strong');
select checks.expect_error($$insert into consistency_flags (application_id,flag_key,check_id,check_type,strength,description) values ('60000000-0000-0000-0000-000000000001','k3','narrative.x','narrative','strong','This looks like fraud')$$,'neutral_wording','an accusing description is refused');
select checks.expect_error($$insert into consistency_flags (application_id,flag_key,check_id,check_type,strength,description) values ('60000000-0000-0000-0000-000000000001','k1','cross_document.arrival_vs_start','cross_document','strong','duplicate key')$$,'duplicate key','the same flag is stored once per application');
select checks.expect_error($$update consistency_flags set status='dismissed', reviewed_by='00000000-0000-0000-0000-0000000000a1', reviewed_at=now() where id='a0000000-0000-0000-0000-000000000001'$$,'dismissal_needs_note','a dismissal needs a note');
select checks.expect_error($$update consistency_flags set status='dismissed', note='  ' , reviewed_by='00000000-0000-0000-0000-0000000000a1', reviewed_at=now() where id='a0000000-0000-0000-0000-000000000001'$$,'dismissal_needs_note','a blank note is not a note');
select checks.expect_error($$update consistency_flags set status='confirmed' where id='a0000000-0000-0000-0000-000000000001'$$,'decision_recorded','a decision records who and when');
insert into identifier_hashes (application_id,kind,hash,source) values ('60000000-0000-0000-0000-000000000001','contact_phone',repeat('ab',32),'form');
select checks.expect_error($$insert into identifier_hashes (application_id,kind,hash) values ('60000000-0000-0000-0000-000000000001','contact_phone','0491 570 006')$$,'identifier_hashes_hash_check','a raw identifier cannot be stored: only a 64-character hash');
select checks.expect_error($$insert into identifier_hashes (application_id,kind,hash) values ('60000000-0000-0000-0000-000000000001','passport_number',repeat('ab',32))$$,'identifier_hashes_kind_check','only the known identifier kinds are hashed');
begin; select checks.as_user('00000000-0000-0000-0000-0000000000a1');
select checks.expect_count('select * from consistency_flags',1,'officer of the organisation sees the flag');
select checks.expect_count('select * from identifier_hashes',1,'officer of the organisation sees the hashes');
select checks.expect_error($$update consistency_flags set status='confirmed' where id='a0000000-0000-0000-0000-000000000001'$$,'permission denied','officers decide through the API, not the table');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000b1');
select checks.expect_count('select * from consistency_flags',0,'another organisation cannot see the flag');
select checks.expect_count('select * from identifier_hashes',0,'another organisation cannot see the hashes');
rollback;
begin; select checks.as_user('00000000-0000-0000-0000-0000000000c1');
select checks.expect_count('select * from consistency_flags',0,'an applicant never sees flags');
select checks.expect_count('select * from document_fingerprints',0,'an applicant never sees fingerprints');
rollback;
drop schema checks cascade;
\echo ALL BEHAVIOUR CHECKS PASSED
