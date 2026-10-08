"""Seed SYNTHETIC demo data.

    python -m seed.run                      # into Supabase (uses .env), demo placeholder values
    python -m seed.run --keep-placeholders  # leave [placeholders] unfilled (code rules report "Unclear")
    python -m seed.run --no-users           # skip creating demo login users

Idempotent: every row has a deterministic id and is only inserted if missing.
Never load real applicant data with this script.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from seed import data
from seed.data import sid

DEMO_VALUES_FILE = Path(__file__).with_name("demo_placeholder_values.json")

DEMO_USERS = {
    "admin": "admin.demo@example.com",
    "officer": "officer.demo@example.com",
    "applicant": "applicant.demo@example.com",
}


def load_demo_values() -> dict[str, Any]:
    values = json.loads(DEMO_VALUES_FILE.read_text())
    values.pop("_warning", None)
    return values


def fill(value: Any, values: dict[str, Any], *, prose: bool = False) -> Any:
    """Replace placeholders. Exact matches take the typed value; inside prose
    they are substituted as text and marked "(demo value)"."""
    if not values:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in values and not prose:
            return values[stripped]
        out = value
        for ph, v in values.items():
            if ph in out:
                text = ", ".join(v) if isinstance(v, list) else str(v)
                out = out.replace(ph, f"{text} (demo value)" if prose else text)
        return out
    if isinstance(value, dict):
        return {k: fill(v, values, prose=prose) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, values, prose=prose) for v in value]
    return value


def insert_missing(store: Any, table: str, row: dict[str, Any], key: str = "id") -> dict[str, Any]:
    existing = store.select(table, eq={key: row[key]}, limit=1)
    return existing[0] if existing else store.insert(table, row)[0]


def ensure_users(store: Any, password: str | None) -> dict[str, str]:
    """Create demo auth users in Supabase (admin API) and set their roles."""
    client = store.client
    password = password or secrets.token_urlsafe(12)
    insert_missing(store, "organisations", data.ORG)  # profiles reference it
    ids: dict[str, str] = {}
    existing = {u.email: u.id for u in client.auth.admin.list_users()}
    for role, email in DEMO_USERS.items():
        if email in existing:
            ids[role] = existing[email]
        else:
            user = client.auth.admin.create_user({"email": email, "password": password, "email_confirm": True})
            ids[role] = user.user.id
            print(f"  created demo user {email} (password: {password})")
        profile = {"role": role, "display_name": f"Demo {role} (fictional)"}
        if role in ("officer", "admin"):
            profile["organisation_id"] = data.ORG_ID
        store.update("profiles", profile, eq={"id": ids[role]})
    return ids


def seed(store: Any, *, demo_values: bool = True, user_ids: dict[str, str] | None = None) -> dict[str, Any]:
    values = load_demo_values() if demo_values else {}
    user_ids = user_ids or {}
    admin_id = user_ids.get("admin", sid("user:admin"))
    now = datetime.now(timezone.utc).isoformat()

    insert_missing(store, "organisations", data.ORG)
    for p in data.PROGRAMS:
        insert_missing(store, "grant_programs", fill(p, values))

    for pack in data.PACKS:
        row = insert_missing(store, "rule_packs", fill(pack, values) | {"status": "draft"})
        if row["status"] == "draft":
            for r in data.RULES:
                if r["rule_pack_id"] == pack["id"]:
                    rule = dict(r)
                    rule["params"] = fill(r["params"], values)
                    rule["rule_text"] = fill(r["rule_text"], values, prose=True)
                    rule["source_clause"] = fill(r["source_clause"], values)
                    rule["source_url"] = fill(r["source_url"], values)
                    insert_missing(store, "rules", rule)
            store.update("rule_packs", {"status": "approved", "approved_by": admin_id, "approved_at": now},
                         eq={"id": pack["id"]})
        # One active pack per program: older approved versions are retired (kept for history).
        for other in store.select("rule_packs", eq={"grant_program_id": pack["grant_program_id"], "status": "approved"}):
            if other["id"] != pack["id"]:
                store.update("rule_packs", {"status": "retired"}, eq={"id": other["id"]})

    for lst in data.REFERENCE_LISTS:
        existing = store.select("reference_lists", eq={"name": lst["name"]}, limit=1)
        if not existing:
            store.insert("reference_lists", lst)
        elif lst["items"] and existing[0].get("items") != lst["items"]:
            store.update("reference_lists", {"items": lst["items"], "source": lst["source"]}, eq={"name": lst["name"]})

    app_ids: dict[str, str] = {}
    for case in data.CASES:
        applicant_id = sid(f"applicant:{case['code']}")
        demo_user = user_ids.get("applicant") if case["code"] == "N01" else None
        if demo_user:
            # The demo applicant login owns one record: move it from any older demo case.
            for other in store.select("applicants", eq={"user_id": demo_user}):
                if other["id"] != applicant_id:
                    store.update("applicants", {"user_id": None}, eq={"id": other["id"]})
        insert_missing(store, "applicants", {
            "id": applicant_id,
            "user_id": demo_user,
            "display_name": case["display_name"],
            "organisation_name": case["organisation_name"],
            "email": case["email"],
        })
        app_id = sid(f"application:{case['code']}")
        app_ids[case["code"]] = app_id
        insert_missing(store, "applications", {
            "id": app_id,
            "grant_program_id": case["program"],
            "rule_pack_id": case["pack"],
            "applicant_id": applicant_id,
            "application_text": case["application_text"],
            "status": "submitted",
            "submitted_at": case.get("submitted_at") or now,
            "language_style_tag": case["style"],
        })
        for i, (declared, text) in enumerate(case["documents"]):
            file_name = f"sample_{declared}_{i + 1}.txt"
            insert_missing(store, "documents", {
                "id": sid(f"document:{case['code']}:{i}"),
                "application_id": app_id,
                "storage_path": f"{app_id}/{file_name}",
                "file_name": file_name,
                "declared_type": declared,
                "extracted_text": text,
                "is_sample": True,
            })
            _upload(store, f"{app_id}/{file_name}", text)
        for j, rec in enumerate(case["register"]):
            status = rec["status"]
            insert_missing(store, "mock_grants_register", {
                "id": sid(f"register:{case['code']}:{j}"),
                "applicant_id": applicant_id,
                "grant_program_id": rec.get("program"),
                "record_type": rec["record_type"],
                "record_name": rec["record_name"],
                "status": status,
                "start_date": date(2025, 1 + j, 1).isoformat(),
                "end_date": date(2027, 1, 1).isoformat() if status == "active" else date(2025, 12, 31).isoformat(),
            })
        case_id = sid(f"eval:{case['code']}")
        insert_missing(store, "evaluation_cases", {
            "id": case_id,
            "case_code": case["code"],
            "application_id": app_id,
            "family_id": case["family_id"],
            "language_style_tag": case["style"],
            "notes": case["notes"],
        })
        for rule_code, expected in case["expected"].items():
            insert_missing(store, "answer_key", {
                "id": sid(f"answer:{case['code']}:{rule_code}"),
                "evaluation_case_id": case_id,
                "rule_id": data.RULE_ID[(case["pack"], rule_code)],
                "expected_status": expected,
                "written_by": data.ANSWER_KEY_AUTHOR,
                "notes": case["notes"],
            })
    return {"applications": app_ids, "placeholders_filled": bool(values)}


def _upload(store: Any, path: str, text: str) -> None:
    client = getattr(store, "client", None)
    if client is None:
        return
    try:
        client.storage.from_("application-documents").upload(
            path, text.encode("utf-8"), {"content-type": "text/plain", "upsert": "true"}
        )
    except Exception:
        pass  # already uploaded, or storage unavailable: rows still carry the text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-placeholders", action="store_true", help="do not fill [placeholders] with demo values")
    parser.add_argument("--no-users", action="store_true", help="do not create demo login users")
    args = parser.parse_args()

    from app.config import get_settings
    from app.store.supabase_store import SupabaseStore

    print("Seeding SYNTHETIC demo data (all people, organisations and documents are fictional).")
    store = SupabaseStore(get_settings())
    user_ids = {} if args.no_users else ensure_users(store, os.environ.get("SEED_DEMO_PASSWORD"))
    if not user_ids:
        print("  --no-users: rule packs are approved_by a placeholder id; this requires that auth user to exist.")
    result = seed(store, demo_values=not args.keep_placeholders, user_ids=user_ids)
    print(f"  {len(result['applications'])} applications seeded; placeholders filled with DEMO values: {result['placeholders_filled']}")
    print("Placeholders used (replace with real values from the guidelines):")
    for ph, where in data.PLACEHOLDERS.items():
        print(f"  {ph:32} {where}")
    print("Assumptions to confirm (Study NT v2):")
    for code, text in data.ASSUMPTIONS.items():
        print(f"  {code:6} {text}")


if __name__ == "__main__":
    main()
