"""Database-level tests: the migrations' triggers and RLS, on a real Postgres.

Set TEST_DATABASE_URL to a Postgres superuser URL of a scratch server, e.g.
    TEST_DATABASE_URL=postgresql://postgres@localhost:55432/postgres
Each test session creates (and drops) its own database, applies a minimal
Supabase stand-in (supabase/tests/local_supabase_stub.sql), then every
migration. Skipped when TEST_DATABASE_URL is not set.
Never point this at a Supabase project.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def db():
    name = f"pytest_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    url = URL.rsplit("/", 1)[0] + f"/{name}"
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute((ROOT / "supabase/tests/local_supabase_stub.sql").read_text())
            for f in sorted((ROOT / "supabase/migrations").glob("*.sql")):
                conn.execute(f.read_text())
        yield url
    finally:
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')


def split_sql(script: str) -> list[str]:
    """Split a psql script into statements (handles '...', $$...$$ and -- comments).

    Statements must be sent one by one, as psql does: in a single multi-statement
    query, a ROLLBACK would also undo everything before it.
    """
    out, buf, i, n = [], [], 0, len(script)
    in_quote = in_dollar = False
    while i < n:
        ch = script[i]
        if not in_quote and not in_dollar and script.startswith("--", i):
            j = script.find("\n", i)
            i = n if j == -1 else j
            continue
        if not in_quote and script.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if not in_dollar and ch == "'":
            in_quote = not in_quote
        if ch == ";" and not in_quote and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def test_behaviour_checks_sql_passes(db):
    """Runs the full SQL behaviour suite (70+ checks of RLS and triggers)."""
    sql = (ROOT / "supabase/tests/behaviour_checks.sql").read_text()
    sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
    notices: list[str] = []
    with psycopg.connect(db, autocommit=True) as conn:
        conn.add_notice_handler(lambda d: notices.append(d.message_primary))
        for stmt in split_sql(sql):
            conn.execute(stmt)
    passes = [n for n in notices if n.startswith("PASS")]
    assert len(passes) >= 70, notices[-3:]


def test_audit_log_update_and_delete_blocked_even_for_owner(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("insert into audit_log (actor_role, action) values ('system', 'db.test')")
        for stmt in ("update audit_log set reason = 'x'", "delete from audit_log", "truncate audit_log"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                conn.execute(stmt)
        conn.execute("set role service_role")
        with pytest.raises(psycopg.Error):
            conn.execute("update audit_log set reason = 'x'")
        with pytest.raises(psycopg.Error):
            conn.execute("delete from audit_log")


REDACTION_RLS_SQL = """
begin;
insert into auth.users (id) values ('00000000-0000-0000-0000-00000000aa01'), ('00000000-0000-0000-0000-00000000aa02'),
                                   ('00000000-0000-0000-0000-00000000aa03');
insert into organisations (id, name) values ('10000000-0000-0000-0000-0000000000aa', 'Redaction Org'),
                                            ('10000000-0000-0000-0000-0000000000bb', 'Other Org');
update profiles set role = 'officer', organisation_id = '10000000-0000-0000-0000-0000000000aa' where id = '00000000-0000-0000-0000-00000000aa01';
update profiles set role = 'officer', organisation_id = '10000000-0000-0000-0000-0000000000bb' where id = '00000000-0000-0000-0000-00000000aa03';
insert into grant_programs (id, organisation_id, name) values ('20000000-0000-0000-0000-0000000000aa', '10000000-0000-0000-0000-0000000000aa', 'P');
insert into rule_packs (id, grant_program_id, version) values ('30000000-0000-0000-0000-0000000000aa', '20000000-0000-0000-0000-0000000000aa', 'v1');
insert into rules (rule_pack_id, rule_code, rule_text, rule_type, check_method) values ('30000000-0000-0000-0000-0000000000aa', 'R1', 'x', 'factual', 'llm');
update rule_packs set status = 'approved', approved_by = '00000000-0000-0000-0000-00000000aa01', approved_at = now() where id = '30000000-0000-0000-0000-0000000000aa';
insert into applicants (id, user_id, display_name) values ('50000000-0000-0000-0000-0000000000aa', '00000000-0000-0000-0000-00000000aa02', 'Fictional');
insert into applications (id, grant_program_id, rule_pack_id, applicant_id, status, submitted_at)
  values ('60000000-0000-0000-0000-0000000000aa', '20000000-0000-0000-0000-0000000000aa', '30000000-0000-0000-0000-0000000000aa',
          '50000000-0000-0000-0000-0000000000aa', 'submitted', now());
insert into redaction_runs (id, application_id, status, detector_version, config_hash)
  values ('70000000-0000-0000-0000-0000000000aa', '60000000-0000-0000-0000-0000000000aa', 'ok', 'test', 'hash');
insert into redaction_token_maps (application_id, run_id, token, entity_type, original_value_encrypted, source, text_key, ordinal)
  values ('60000000-0000-0000-0000-0000000000aa', '70000000-0000-0000-0000-0000000000aa', '[PERSON_1]', 'PERSON', 'v1:abcd:Zm9v',
          'fields:applicant_name', 'application_text', 0);
"""


def _as(conn, user_id: str | None) -> None:
    if user_id is None:
        conn.execute("set local role anon")
        return
    conn.execute("select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{user_id}","role":"authenticated"}}',))
    conn.execute("set local role authenticated")


def test_redaction_token_map_rls(db):
    with psycopg.connect(db) as conn:  # one transaction, rolled back at the end
        for stmt in split_sql(REDACTION_RLS_SQL):
            if stmt != "begin":
                conn.execute(stmt)
        conn.execute("savepoint s")
        _as(conn, "00000000-0000-0000-0000-00000000aa01")  # officer, same organisation
        assert conn.execute("select count(*) from redaction_token_maps").fetchone()[0] == 1
        assert conn.execute("select count(*) from redaction_runs").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into redaction_token_maps (application_id, token, entity_type, original_value_encrypted, "
                         "source, text_key, ordinal) values ('60000000-0000-0000-0000-0000000000aa', '[X_1]', 'PERSON', "
                         "'v1:a:b', 's', 't', 0)")
        conn.execute("rollback to savepoint s")
        for who in ("00000000-0000-0000-0000-00000000aa02", "00000000-0000-0000-0000-00000000aa03"):  # applicant; other org
            _as(conn, who)
            assert conn.execute("select count(*) from redaction_token_maps").fetchone()[0] == 0, who
            assert conn.execute("select count(*) from redaction_runs").fetchone()[0] == 0, who
            conn.execute("rollback to savepoint s")
        _as(conn, None)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from redaction_token_maps")
        conn.execute("rollback to savepoint s")
        with pytest.raises(psycopg.errors.CheckViolation):  # plaintext is rejected by the database itself
            conn.execute("insert into redaction_token_maps (application_id, token, entity_type, original_value_encrypted, "
                         "source, text_key, ordinal) values ('60000000-0000-0000-0000-0000000000aa', '[PERSON_2]', 'PERSON', "
                         "'Linh Tran', 's', 't', 1)")
        conn.rollback()
