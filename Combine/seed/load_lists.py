"""Load an official reference list into Supabase (officers can also do this on the Reference lists screen).

    python -m seed.load_lists --sopl seed/lists/nt_skilled_occupation_priority_list_2026.txt            # preview only
    python -m seed.load_lists --sopl seed/lists/nt_skilled_occupation_priority_list_2026.txt --save --edition "31 August 2026"

The file is the list's text, one row per line: OSCA code, occupation, skill level. Headings such as
"High priority occupations" set the tier; page headers and footers are ignored. A .csv or one-name-per-line
.txt also works. Needs migration 0012 applied first. Re-assessing an application after a list changes
creates a fresh run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.services.reference_lists import SOPL, items_of, parse_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sopl", type=Path, required=True, help="text or CSV file of the NT Skilled Occupation Priority List")
    parser.add_argument("--edition", default="", help='for example "31 August 2026"')
    parser.add_argument("--save", action="store_true", help="write to Supabase (default: preview only)")
    args = parser.parse_args()

    entries, warnings = parse_text(args.sopl.read_text(encoding="utf-8"))
    print(f"Read {len(entries)} entries from {args.sopl.name}. First 10:")
    for e in entries[:10]:
        print(f"  {e['code'] or '-':>6}  {e['name']}  (level {e['skill_level'] or '-'}, {e['tier'] or 'no tier'})")
    for w in warnings:
        print(f"  WARNING: {w}")
    if not args.save:
        print("\nPreview only. Check the list above, then re-run with --save.")
        return
    if not entries:
        raise SystemExit("No entries found; nothing saved.")

    from app.config import get_settings
    from app.store.base import now_iso
    from app.store.supabase_store import SupabaseStore

    store = SupabaseStore(get_settings())
    values = {"items": items_of(entries), "entries": entries, "edition": args.edition or None,
              "source": f"Loaded from {args.sopl.name}", "updated_at": now_iso()}
    if store.select("reference_lists", eq={"name": SOPL}, limit=1):
        store.update("reference_lists", values, eq={"name": SOPL})
    else:
        store.insert("reference_lists", {"name": SOPL, "description": "NT Skilled Occupation Priority List (Study NT rule S3)", **values})
    print(f"Saved {len(entries)} entries to reference list '{SOPL}'. Re-assess applications to use it.")


if __name__ == "__main__":
    main()
