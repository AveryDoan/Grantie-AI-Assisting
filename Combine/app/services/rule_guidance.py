"""Rule-to-document map and the "what the officer needs to verify" sentence for each rule (config/rule_guidance.yaml).
Data, not code: a new rule pack adds a block to the file. Nothing here decides a rule."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[2] / "config" / "rule_guidance.yaml"
DOC_LABELS = {
    "coe": "Confirmation of Enrolment", "offer_letter": "Letter of offer", "travel_booking": "Travel booking or itinerary",
    "referee_letter": "Letter of support", "headshot": "Headshot", "biography": "Biography", "resume": "Resume",
    "visa": "Visa grant notice", "certificate": "Certificate", "transcript": "Academic transcript", "passport": "Passport",
    "application_responses": "Application responses", "certified_translation": "Certified translation",
}


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    try:
        return (yaml.safe_load(PATH.read_text()) or {}).get("rules") or {}
    except FileNotFoundError:
        return {}


def _norm(t: str | None) -> str:
    return (t or "").strip().lower().replace(" ", "_")


def for_rule(code: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """{rule_documents: [{type, label, file_name, status}], verify_note} for one rule code against the application's live documents."""
    g = _load().get(code) or {}
    live = [d for d in documents if not d.get("superseded")]
    out = []
    for t in g.get("documents") or []:
        mine = [d for d in live if _norm(d.get("declared_type")) == t or _norm(d.get("detected_type")) == t]
        if not mine:
            out.append({"type": t, "label": DOC_LABELS.get(t, t.replace("_", " ").title()), "file_name": None, "status": "Not provided", "document_id": None})
            continue
        for d in mine:
            ok = d.get("extraction_status") == "ok"
            out.append({"type": t, "label": DOC_LABELS.get(t, t.replace("_", " ").title()), "file_name": d.get("file_name"), "document_id": d.get("id"),
                        "status": "Uploaded and readable" if ok else "Uploaded, text not read (a person must read it)"})
    return {"rule_documents": out, "verify_note": g.get("verify")}
