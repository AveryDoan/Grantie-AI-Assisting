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
