"""Database triggers for outcomes (migration 0019), on a real Postgres. Brief tests 5, 6, 7 and 14 at the database level.

Skipped when TEST_DATABASE_URL is not set (see tests/test_database.py). Every check runs as the service role the API uses,
so the rules hold even when the API is bypassed. Synthetic fixtures only.
"""

from __future__ import annotations

import pytest

from tests.test_database import URL, db, split_sql  # noqa: F401  (db is a fixture)

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

OFFICER = "00000000-0000-0000-0000-00000000f001"
APPLICANT = "00000000-0000-0000-0000-00000000f002"     # has a profile, but not an officer (stands in for any non-officer)
NOBODY = "00000000-0000-0000-0000-00000000f003"        # an auth user with no officer role (a system job, the AI)
INELIGIBLE, ELIGIBLE = "60000000-0000-0000-0000-00000000f0a1", "60000000-0000-0000-0000-00000000f0b1"
F_A_R1, F_A_M1 = "80000000-0000-0000-0000-00000000f0a1", "80000000-0000-0000-0000-00000000f0a2"
F_B_R1, F_B_M1 = "80000000-0000-0000-0000-00000000f0b1", "80000000-0000-0000-0000-00000000f0b2"

SETUP = f"""
insert into auth.users (id) values ('{OFFICER}'), ('{APPLICANT}'), ('{NOBODY}');
insert into organisations (id, name) values ('10000000-0000-0000-0000-00000000f00a', 'Outcome Org');
update profiles set role = 'officer', organisation_id = '10000000-0000-0000-0000-00000000f00a' where id = '{OFFICER}';
insert into grant_programs (id, organisation_id, name) values ('20000000-0000-0000-0000-00000000f00a', '10000000-0000-0000-0000-00000000f00a', 'P');
insert into rule_packs (id, grant_program_id, version) values ('30000000-0000-0000-0000-00000000f00a', '20000000-0000-0000-0000-00000000f00a', 'v1');
insert into rules (id, rule_pack_id, rule_code, rule_text, rule_type, check_method, params) values
  ('40000000-0000-0000-0000-00000000f001', '30000000-0000-0000-0000-00000000f00a', 'R1', 'Lives in the NT', 'factual', 'llm', '{{}}'),
  ('40000000-0000-0000-0000-00000000f002', '30000000-0000-0000-0000-00000000f00a', 'M1', 'Leadership', 'judgement', 'llm', '{{"section": "merit"}}');
update rule_packs set status = 'approved', approved_by = '{OFFICER}', approved_at = now() where id = '30000000-0000-0000-0000-00000000f00a';
insert into applicants (id, user_id, display_name) values ('50000000-0000-0000-0000-00000000f00a', '{APPLICANT}', 'Fictional');
insert into applications (id, grant_program_id, rule_pack_id, applicant_id, status, submitted_at, application_text) values
  ('{INELIGIBLE}', '20000000-0000-0000-0000-00000000f00a', '30000000-0000-0000-0000-00000000f00a', '50000000-0000-0000-0000-00000000f00a', 'submitted', now(), '{{"q": "I live in Perth."}}'),
  ('{ELIGIBLE}', '20000000-0000-0000-0000-00000000f00a', '30000000-0000-0000-0000-00000000f00a', '50000000-0000-0000-0000-00000000f00a', 'submitted', now(), '{{"q": "I live in Darwin."}}');
insert into assessment_runs (id, application_id, rule_pack_id, model_name, prompt_version) values
  ('70000000-0000-0000-0000-00000000f0a1', '{INELIGIBLE}', '30000000-0000-0000-0000-00000000f00a', 'm', 'p'),
  ('70000000-0000-0000-0000-00000000f0b1', '{ELIGIBLE}', '30000000-0000-0000-0000-00000000f00a', 'm', 'p');
insert into findings (id, run_id, application_id, rule_id, ai_status, check_source, evidence_quote, quote_verified, is_valid, confidence) values
  ('{F_A_R1}', '70000000-0000-0000-0000-00000000f0a1', '{INELIGIBLE}', '40000000-0000-0000-0000-00000000f001', 'Not met', 'llm', 'I live in Perth.', true, true, 'high'),
  ('{F_A_M1}', '70000000-0000-0000-0000-00000000f0a1', '{INELIGIBLE}', '40000000-0000-0000-0000-00000000f002', 'Evidence only', 'llm', null, false, true, null),
  ('{F_B_R1}', '70000000-0000-0000-0000-00000000f0b1', '{ELIGIBLE}', '40000000-0000-0000-0000-00000000f001', 'Met', 'llm', 'I live in Darwin.', true, true, 'high'),
  ('{F_B_M1}', '70000000-0000-0000-0000-00000000f0b1', '{ELIGIBLE}', '40000000-0000-0000-0000-00000000f002', 'Evidence only', 'llm', null, false, true, null);
update assessment_runs set status = 'complete', finished_at = now();
"""


@pytest.fixture
def conn(db):  # noqa: F811
    """One transaction per test, rolled back at the end. Runs as postgres (the service role path the API uses)."""
    with psycopg.connect(db) as c:
        for stmt in split_sql(SETUP):
            c.execute(stmt)
        yield c
        c.rollback()


def blocked(c, sql: str, pattern: str) -> None:
    c.execute("savepoint t")
    with pytest.raises(psycopg.Error, match=pattern):
        c.execute(sql)
    c.execute("rollback to savepoint t")


def not_met(c) -> None:
    c.execute(f"insert into officer_reviews (finding_id, officer_id, action, final_status, reason) "
              f"values ('{F_A_R1}', '{OFFICER}', 'confirm', 'Not met', 'The address is in Perth')")


def eligible(c, *, merit_reason: str | None = "Led a youth club for two years") -> None:
    c.execute(f"insert into officer_reviews (finding_id, officer_id, action, final_status) values ('{F_B_R1}', '{OFFICER}', 'confirm', 'Met')")
    if merit_reason is not None:
        c.execute(f"insert into merit_marks (application_id, rule_code, mark, reason, officer_id) values ('{ELIGIBLE}', 'M1', 70, %s, '{OFFICER}')",
                  (merit_reason,))
    c.execute(f"update applications set eligibility_result = 'eligible' where id = '{ELIGIBLE}'")


# ------------------------------------------------------------------ 1. ineligible needs a confirmed Not met

def test_ineligible_needs_an_officer_confirmed_not_met(conn):
    blocked(conn, f"update applications set eligibility_result = 'ineligible' where id = '{INELIGIBLE}'", "OUTCOME_BLOCKED")
    blocked(conn, f"update applications set outcome_status = 'ineligible' where id = '{INELIGIBLE}'", "OUTCOME_BLOCKED")
    not_met(conn)
    conn.execute(f"update applications set eligibility_result = 'ineligible', outcome_status = 'ineligible' where id = '{INELIGIBLE}'")


def test_eligible_needs_every_eligibility_rule_decided_met(conn):
    blocked(conn, f"update applications set eligibility_result = 'eligible' where id = '{ELIGIBLE}'", "OUTCOME_BLOCKED")
    eligible(conn)


def test_an_officer_cannot_set_the_outcome_directly(conn):
    not_met(conn)
    conn.execute("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{OFFICER}","role":"authenticated"}}',))
    conn.execute("set local role authenticated")
    blocked(conn, f"update applications set outcome_status = 'ineligible' where id = '{INELIGIBLE}'", "set by the officer's recorded actions")


# ------------------------------------------------------------------ steps not assessed

def test_steps_are_skipped_only_after_a_not_met_and_only_by_an_officer(conn):
    sql = "insert into steps_not_assessed (application_id, step, reason, officer_id) values ('{a}', 'merit', 'Not assessed (ineligible)', '{o}')"
    blocked(conn, sql.format(a=INELIGIBLE, o=OFFICER), "OUTCOME_BLOCKED")
    not_met(conn)
    blocked(conn, sql.format(a=INELIGIBLE, o=NOBODY), "OFFICER_ONLY")
    conn.execute(sql.format(a=INELIGIBLE, o=OFFICER))
    blocked(conn, f"delete from steps_not_assessed where application_id = '{INELIGIBLE}'", "cleared, never deleted")
    # the sign-off guard no longer waits for merit on this application
    assert conn.execute(f"select private.unreviewed_findings('{INELIGIBLE}')").fetchone()[0] == 0


# ------------------------------------------------------------------ 2. decline letters cite only confirmed Not met findings

def test_a_decline_letter_may_only_cite_confirmed_not_met_findings(conn):
    sql = "insert into letters (application_id, version, body_text, kind, source_finding_ids) values ('{a}', {v}, 'x', 'decline', array['{f}']::uuid[])"
    blocked(conn, sql.format(a=INELIGIBLE, v=1, f=F_A_R1), "LETTER_BLOCKED")         # AI said Not met; no officer yet
    not_met(conn)
    blocked(conn, sql.format(a=INELIGIBLE, v=1, f=F_A_M1), "LETTER_BLOCKED")         # not a Not met finding
    conn.execute(sql.format(a=INELIGIBLE, v=1, f=F_A_R1))


# ------------------------------------------------------------------ 5, 6 and 7. release

def _released_letter(c) -> str:
    not_met(c)
    c.execute(f"insert into steps_not_assessed (application_id, step, reason, officer_id) values ('{INELIGIBLE}', 'merit', 'Not assessed (ineligible)', '{OFFICER}')")
    lid = c.execute(f"insert into letters (application_id, version, body_text, kind, source_finding_ids) values "
                    f"('{INELIGIBLE}', 1, 'x', 'decline', array['{F_A_R1}']::uuid[]) returning id").fetchone()[0]
    c.execute(f"insert into sign_offs (application_id, officer_id, statement_acknowledged) values ('{INELIGIBLE}', '{OFFICER}', true)")
    return lid


def test_release_needs_sign_off_a_viewed_letter_and_passing_checks(conn):
    lid = _released_letter(conn)
    release = (f"update letters set status = 'approved', approved_by = '{OFFICER}', approved_at = now(), released_at = now(), "
               f"released_by = '{OFFICER}', signed_off_by = '{OFFICER}', signed_off_at = now(){{extra}} where id = '{lid}'")
    blocked(conn, release.format(extra=", checks_passed = true"), "RELEASE_BLOCKED")                         # not viewed
    blocked(conn, release.format(extra=f", letter_viewed_at = now(), viewed_by = '{OFFICER}'"), "RELEASE_BLOCKED")  # checks fail
    blocked(conn, f"update letters set status = 'approved', approved_by = '{OFFICER}', approved_at = now() where id = '{lid}'", "RELEASE_BLOCKED")
    blocked(conn, release.replace(f"released_by = '{OFFICER}'", f"released_by = '{NOBODY}'")
            .format(extra=f", checks_passed = true, letter_viewed_at = now(), viewed_by = '{OFFICER}'"), "OFFICER_ONLY")
    conn.execute(release.format(extra=f", checks_passed = true, letter_viewed_at = now(), viewed_by = '{OFFICER}'"))
    # 14. after release nothing changes
    blocked(conn, f"update letters set body_text = 'changed' where id = '{lid}'", "approved and cannot be changed|released")
    blocked(conn, f"delete from letters where id = '{lid}'", "released letter cannot be deleted")


def test_only_an_officer_signs_off_or_records_a_decision(conn):
    blocked(conn, f"insert into officer_reviews (finding_id, officer_id, action, final_status, reason) "
                  f"values ('{F_A_R1}', '{NOBODY}', 'confirm', 'Not met', 'x')", "OFFICER_ONLY")
    blocked(conn, f"insert into officer_reviews (finding_id, officer_id, action, final_status, reason) "
                  f"values ('{F_A_R1}', '{APPLICANT}', 'confirm', 'Not met', 'x')", "OFFICER_ONLY")
    not_met(conn)
    blocked(conn, f"insert into sign_offs (application_id, officer_id, statement_acknowledged) values ('{INELIGIBLE}', '{NOBODY}', true)",
            "OFFICER_ONLY")


# ------------------------------------------------------------------ 3, 4 and 6. round decisions

RD = ("insert into round_decisions (application_id, decision, decided_by, authority, decided_at, reference, reason_note, created_by) "
      "values ('{a}', '{d}', 'Assessment panel', 'Panel chair', current_date, '{ref}', {note}, '{o}')")
NOTE = "'Funding was limited to five places; the panel recommended other applications.'"


def test_round_decision_only_for_eligible_applications_with_complete_merit(conn):
    blocked(conn, RD.format(a=ELIGIBLE, d="selected", ref="Minute 4.2", note="null", o=OFFICER), "ROUND_DECISION_BLOCKED")
    not_met(conn)
    blocked(conn, RD.format(a=INELIGIBLE, d="selected", ref="Minute 4.2", note="null", o=OFFICER), "ROUND_DECISION_BLOCKED")
    eligible(conn, merit_reason=None)                         # merit not marked yet
    blocked(conn, RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.2", note=NOTE, o=OFFICER), "ROUND_DECISION_BLOCKED")
    conn.execute(f"insert into merit_marks (application_id, rule_code, mark, reason, officer_id) values ('{ELIGIBLE}', 'M1', 70, 'Led a club', '{OFFICER}')")
    blocked(conn, RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.2", note="'too short'", o=OFFICER), "not_selected_has_reason")
    blocked(conn, RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="  ", note=NOTE, o=OFFICER), "round_decisions_reference_check")
    blocked(conn, RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.2", note=NOTE, o=NOBODY), "OFFICER_ONLY")
    conn.execute(RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.2", note=NOTE, o=OFFICER))
    blocked(conn, f"update round_decisions set decision = 'selected' where application_id = '{ELIGIBLE}'", "append-only")
    blocked(conn, f"delete from round_decisions where application_id = '{ELIGIBLE}'", "append-only")
    conn.execute(f"update applications set outcome_status = 'not_selected_competitive' where id = '{ELIGIBLE}'")
    blocked(conn, f"update applications set outcome_status = 'selected' where id = '{ELIGIBLE}'", "OUTCOME_BLOCKED")


def test_a_not_selected_letter_needs_the_round_decision(conn):
    sql = f"insert into letters (application_id, version, body_text, kind) values ('{ELIGIBLE}', 1, 'x', 'not_selected')"
    eligible(conn)
    blocked(conn, sql, "LETTER_BLOCKED")
    conn.execute(RD.format(a=ELIGIBLE, d="selected", ref="Minute 4.2", note="null", o=OFFICER))
    blocked(conn, sql, "LETTER_BLOCKED")                                        # the latest decision is Selected
    conn.execute(RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.3", note=NOTE, o=OFFICER))
    conn.execute(sql)


def test_after_release_a_new_decision_needs_a_reconsideration(conn):
    eligible(conn)
    conn.execute(RD.format(a=ELIGIBLE, d="not_selected_competitive", ref="Minute 4.2", note=NOTE, o=OFFICER))
    lid = conn.execute(f"insert into letters (application_id, version, body_text, kind) values ('{ELIGIBLE}', 1, 'x', 'not_selected') returning id").fetchone()[0]
    conn.execute(f"insert into sign_offs (application_id, officer_id, statement_acknowledged) values ('{ELIGIBLE}', '{OFFICER}', true)")
    conn.execute(f"update letters set status = 'approved', approved_by = '{OFFICER}', approved_at = now(), released_at = now(), released_by = '{OFFICER}', "
                 f"signed_off_by = '{OFFICER}', signed_off_at = now(), checks_passed = true, letter_viewed_at = now(), viewed_by = '{OFFICER}' where id = '{lid}'")
    blocked(conn, RD.format(a=ELIGIBLE, d="selected", ref="Minute 5.1", note="null", o=OFFICER), "ROUND_DECISION_BLOCKED")
    # a reconsideration: the sign-off is reopened with a reason (kept in sign_off_history)
    so = conn.execute(f"select officer_id, signed_at from sign_offs where application_id = '{ELIGIBLE}'").fetchone()
    # (one test transaction has a single now(); a real reconsideration happens later than the release)
    conn.execute(f"insert into sign_off_history (application_id, version, officer_id, signed_at, reopened_by, reopened_at, reason) "
                 f"values ('{ELIGIBLE}', 1, %s, %s, '{OFFICER}', now() + interval '1 second', 'Review requested by the applicant')", so)
    conn.execute(f"delete from sign_offs where application_id = '{ELIGIBLE}'")
    conn.execute(f"update applications set status = 'in_review' where id = '{ELIGIBLE}'")
    conn.execute(RD.format(a=ELIGIBLE, d="selected", ref="Minute 5.1", note="null", o=OFFICER))
    assert conn.execute(f"select max(version) from round_decisions where application_id = '{ELIGIBLE}'").fetchone()[0] == 2
