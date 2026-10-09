"""Load the synthetic consistency cases (F01 to F10) into a store, the same way the app stores an upload.

Each document goes through intake.prepare_upload: PDF metadata is read FIRST (derived signals only) and then
stripped from the stored copy, exactly as for a real upload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from seed import data
from seed.data import sid
from seed.fraud.generate import ROOT, write_case
from seed.fraud.scenarios import SUBMITTED_AT, build

BUCKET = "application-documents"


def expected() -> dict[str, dict[str, Any]]:
    """code -> {control, should_raise, may_raise, should_not_raise}"""
    return {sc.code: {"control": sc.control, "should_raise": sc.should, "may_raise": sc.allow, "should_not_raise": sc.should_not}
            for sc in build()}


def seed_fraud(store: Any, *, consistency: bool = True) -> dict[str, str]:
    """Insert F01 to F10. Returns code -> application id."""
    from app.services.intake import prepare_upload

    ids: dict[str, str] = {}
    for sc in build():
        folder = ROOT / sc.code
        if not (folder / "form.json").exists():
            write_case(sc)
        form = json.loads((folder / "form.json").read_text(encoding="utf-8"))
        applicant_id, app_id = sid(f"applicant:{sc.code}"), sid(f"application:{sc.code}")
        ids[sc.code] = app_id
        if store.select("applications", eq={"id": app_id}, limit=1):
            continue
        store.insert("applicants", {"id": applicant_id, "display_name": sc.name, "organisation_name": None, "email": sc.email})
        store.insert("applications", {
            "id": app_id, "grant_program_id": data.SNT_PROGRAM, "rule_pack_id": data.SNT_PACK, "applicant_id": applicant_id,
            "application_text": form["application_text"], "status": "submitted", "submitted_at": SUBMITTED_AT,
        })
        for i, item in enumerate(form["documents"]):
            raw = (folder / item["file"]).read_bytes()
            doc_id = sid(f"document:{sc.code}:{i}")
            stored, extracted, signals, kind = prepare_upload(raw, item["file"], applicant_name=sc.name, doc_id=doc_id, read_signals=consistency)
            path = f"{app_id}/{doc_id}"
            upload = getattr(store, "upload", None)
            if upload:
                try:
                    upload(BUCKET, path, stored, "application/pdf")
                except Exception:
                    pass  # already uploaded; the row still carries the text
            row = {"id": doc_id, "application_id": app_id, "storage_path": path, "file_name": f"sample_{item['declared_type'].replace(' ', '_')}_{i + 1}.pdf",
                   "declared_type": item["declared_type"], "extracted_text": extracted.text or None, "is_sample": True}
            if consistency:
                row["integrity_signals"] = signals
            store.insert("documents", row)
    return ids
