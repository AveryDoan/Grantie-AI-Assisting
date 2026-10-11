"""Generate the synthetic consistency cases as files: PDFs, form JSON and expected flags.

    python -m seed.fraud.generate          # writes seed/fraud/generated/<code>/...

Output is deterministic (no clock, no randomness), so it can be regenerated and compared. Nothing is hand-edited.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from seed.fraud.pdfkit import make_pdf
from seed.fraud.scenarios import SUBMITTED_AT, Scenario, build, build_documents

ROOT = Path(__file__).resolve().parent / "generated"


def slug(kind: str) -> str:
    return kind.replace(" ", "_")


def write_case(sc: Scenario, root: Path = ROOT) -> Path:
    folder = root / sc.code
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    files = []
    counts: dict[str, int] = {}
    for doc in sc.docs:
        counts[doc.kind] = counts.get(doc.kind, 0) + 1
        name = f"{slug(doc.kind)}_{counts[doc.kind]}.pdf"
        (folder / name).write_bytes(make_pdf(doc.lines, info=doc.info))
        files.append({"file": name, "declared_type": doc.kind})
    (folder / "form.json").write_text(json.dumps({
        "code": sc.code, "title": sc.title, "display_name": sc.name, "email": sc.email, "submitted_at": SUBMITTED_AT,
        "application_text": {"fields": sc.fields, "answers": sc.answers}, "documents": files, "notes": sc.notes,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "expected_flags.json").write_text(json.dumps({
        "code": sc.code, "control": sc.control, "should_raise": sc.should, "may_raise": sc.allow, "should_not_raise": sc.should_not,
    }, indent=2) + "\n", encoding="utf-8")
    return folder


def main() -> None:
    cases = [*build(), *build_documents()]
    ROOT.mkdir(parents=True, exist_ok=True)
    for sc in cases:
        folder = write_case(sc)
        print(f"{sc.code}: {len(sc.docs)} documents -> {folder.relative_to(ROOT.parent.parent.parent)}")
    print(f"Wrote {len(cases)} synthetic cases. All data is fictional.")


if __name__ == "__main__":
    main()
