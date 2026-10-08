"""Load official reference lists into Supabase.

    python -m seed.load_lists --sopl nt-skilled-occupation-priority-list.pdf   # preview only
    python -m seed.load_lists --sopl nt-skilled-occupation-priority-list.pdf --save
    python -m seed.load_lists --sopl occupations.txt --save   # one occupation per line (or a CSV's first column)

The NT Skilled Occupation Priority List is published as a PDF on nt.gov.au.
The site blocks automated downloads, so download it in a browser first.
Rules that use a list (S1, S3) return "Unclear" until it is loaded, and
re-assessing an application after loading a list creates a fresh run.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

SOPL = "nt_skilled_occupation_priority_list"
SOPL_SOURCE = "https://nt.gov.au/_media/docs/employing-people-and-jobs/for-employers-in-the-nt/nt-skilled-occupation-priority-list.pdf"

_SKIP = re.compile(
    r"skilled occupation|priority list|^occupation\b|^anzsco\b|^page \d|^\d+$|updated|northern territory government|"
    r"nt\.gov\.au|^code\b|^skill level|^assessing authority|^visa|^stream",
    re.IGNORECASE,
)


def occupations_from_text(text: str) -> list[str]:
    """Pull occupation names out of extracted PDF text (ANZSCO codes are dropped)."""
    out: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\b\d{6}\b", " ", raw)          # ANZSCO code
        line = re.sub(r"\s{2,}", " ", line).strip(" -–•*\t")
        if len(line) < 3 or len(line) > 90 or not re.search(r"[A-Za-z]{3}", line) or _SKIP.search(line):
            continue
        if line not in out:
            out.append(line)
    return out


def read_occupations(path: Path) -> list[str]:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # pip install pypdf

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        return occupations_from_text(text)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            return [row[0].strip() for row in csv.reader(fh) if row and row[0].strip()]
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sopl", type=Path, required=True, help="PDF, CSV or text file of the NT Skilled Occupation Priority List")
    parser.add_argument("--save", action="store_true", help="write to Supabase (default: preview only)")
    args = parser.parse_args()

    items = read_occupations(args.sopl)
    print(f"Found {len(items)} occupations in {args.sopl.name}. First 15:")
    for item in items[:15]:
        print(f"  - {item}")
    if not args.save:
        print("\nPreview only. Check the list above, then re-run with --save.")
        return
    if not items:
        raise SystemExit("No occupations found; nothing saved.")

    from app.config import get_settings
    from app.store.supabase_store import SupabaseStore

    store = SupabaseStore(get_settings())
    source = f"{SOPL_SOURCE} (loaded from {args.sopl.name})"
    if store.select("reference_lists", eq={"name": SOPL}, limit=1):
        store.update("reference_lists", {"items": items, "source": source}, eq={"name": SOPL})
    else:
        store.insert("reference_lists", {"name": SOPL, "description": "NT Skilled Occupation Priority List (Study NT rule S3)",
                                         "items": items, "source": source})
    print(f"Saved {len(items)} occupations to reference list '{SOPL}'. Re-assess applications to use it.")


if __name__ == "__main__":
    main()
