"""The weighted total in SQL (private.weighted_total, view application_scores) equals the Python arithmetic, in every policy and
for every case: complete, missing mark, Not assessed, an unweighted criterion. Needs TEST_DATABASE_URL (see tests/test_database.py)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.services import scores
from tests.test_database import URL, db, split_sql  # noqa: F401  (db is a fixture)

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

OFFICER = "00000000-0000-0000-0000-00000000e001"
WEIGHTS = {"M1": 40, "M2": 30, "M3": 20, "M4": 10, "M5": None}      # M5 is marked but not scored
CASES = {
    "complete": {"M1": (82, False), "M2": (75, False), "M3": (60, False), "M4": (90, False), "M5": (100, False)},
    "decimal": {"M1": (83, False), "M2": (71, False), "M3": (59, False), "M4": (97, False), "M5": (0, False)},
    "missing": {"M1": (82, False), "M2": (75, False)},
    "not_assessed": {"M1": (80, False), "M2": (None, True), "M3": (60, False), "M4": (90, False), "M5": (50, False)},
    "unweighted_na": {"M1": (80, False), "M2": (70, False), "M3": (60, False), "M4": (90, False), "M5": (None, True)},
    "all_zero": {"M1": (0, False), "M2": (0, False), "M3": (0, False), "M4": (0, False), "M5": (0, False)},
}


def test_sql_and_python_totals_agree(db):  # noqa: F811
    with psycopg.connect(db, autocommit=True) as c:
        c.execute(f"insert into auth.users (id) values ('{OFFICER}')")
        c.execute("insert into organisations (id, name) values ('10000000-0000-0000-0000-00000000e00a', 'Scores Org')")
        c.execute(f"update profiles set role = 'officer', organisation_id = '10000000-0000-0000-0000-00000000e00a' where id = '{OFFICER}'")
        c.execute("insert into grant_programs (id, organisation_id, name) values ('20000000-0000-0000-0000-00000000e00a', '10000000-0000-0000-0000-00000000e00a', 'P')")
        c.execute("insert into applicants (id, display_name) values ('50000000-0000-0000-0000-00000000e00a', 'Fictional')")
        n = 0
        for pi, policy in enumerate(("block", "zero", "reweight")):
            pack = f"30000000-0000-0000-0000-00000000e0{pi}1"
            c.execute("insert into rule_packs (id, grant_program_id, version, letter_config) values (%s, '20000000-0000-0000-0000-00000000e00a', %s, %s)",
                      (pack, f"v{pi}", json.dumps({"not_assessed_policy": policy})))
            for code, w in WEIGHTS.items():
                params = {"section": "merit", **({"weight": w} if w else {})}
                c.execute("insert into rules (rule_pack_id, rule_code, rule_text, rule_type, check_method, params) values (%s, %s, 'x', 'judgement', 'llm', %s)",
                          (pack, code, json.dumps(params)))
            c.execute("update rule_packs set status = 'approved', approved_by = %s, approved_at = now() where id = %s", (OFFICER, pack))
            crit = {"items": [{"code": k, "title": k, "weight": None if w is None else Decimal(w)} for k, w in WEIGHTS.items()], "error": None,
                    "weight_sum": Decimal(100), "normalised": False}
            for name, marks in CASES.items():
                n += 1
                app = f"60000000-0000-0000-0000-0000000e{n:04d}"
                c.execute("insert into applications (id, grant_program_id, rule_pack_id, applicant_id, status, submitted_at) values "
                          "(%s, '20000000-0000-0000-0000-00000000e00a', %s, '50000000-0000-0000-0000-00000000e00a', 'submitted', now())", (app, pack))
                for code, (mark, na) in marks.items():
                    if na:
                        c.execute("insert into merit_marks (application_id, rule_code, not_assessed, officer_id) values (%s, %s, true, %s)", (app, code, OFFICER))
                    else:
                        c.execute("insert into merit_marks (application_id, rule_code, mark, reason, officer_id) values (%s, %s, %s, 'a reason', %s)", (app, code, mark, OFFICER))
                got = c.execute("select private.weighted_total(%s)", (app,)).fetchone()[0]
                py = scores.calculate(crit, {k: {"mark": m, "not_assessed": na} for k, (m, na) in marks.items()}, policy)["total"]
                assert (got is None) == (py is None), f"{policy}/{name}: SQL {got} vs Python {py}"
                if py is not None:
                    assert Decimal(got) == py or abs(Decimal(got) - py) < Decimal("1e-12"), f"{policy}/{name}: SQL {got} vs Python {py}"
                    assert str(Decimal(got).quantize(Decimal('0.1'), rounding='ROUND_HALF_UP')) == scores.show(py)
        # a weight that is present but not positive makes the total impossible, in SQL as in Python
        c.execute("insert into rule_packs (id, grant_program_id, version) values ('30000000-0000-0000-0000-00000000e0ff', '20000000-0000-0000-0000-00000000e00a', 'bad')")
        c.execute("insert into rules (rule_pack_id, rule_code, rule_text, rule_type, check_method, params) values ('30000000-0000-0000-0000-00000000e0ff', 'M1', 'x', 'judgement', 'llm', '{\"section\": \"merit\", \"weight\": 0}')")
        c.execute("update rule_packs set status = 'approved', approved_by = %s, approved_at = now() where id = '30000000-0000-0000-0000-00000000e0ff'", (OFFICER,))
        c.execute("insert into applications (id, grant_program_id, rule_pack_id, applicant_id, status, submitted_at) values "
                  "('60000000-0000-0000-0000-0000000eff01', '20000000-0000-0000-0000-00000000e00a', '30000000-0000-0000-0000-00000000e0ff', '50000000-0000-0000-0000-00000000e00a', 'submitted', now())")
        c.execute("insert into merit_marks (application_id, rule_code, mark, reason, officer_id) values ('60000000-0000-0000-0000-0000000eff01', 'M1', 50, 'r', %s)", (OFFICER,))
        assert c.execute("select private.weighted_total('60000000-0000-0000-0000-0000000eff01')").fetchone()[0] is None
        # the view exists and returns nothing to a caller who is not staff
        assert c.execute("select count(*) from public.application_scores").fetchone()[0] == 0
