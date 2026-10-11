"""Applicant pre-submission check (plain code, no LLM).

Reports missing fields, missing documents and documents that look like a
different type from what was declared. It never says whether the applicant
is eligible, and it ALWAYS offers to submit anyway or to ask a person.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.documents import DOC_LABEL_SHORT, detect_type, normalise_declared
from app.pipeline.rules_loader import load_active_pack_for_program
from app.services.access import Actor, require_role
from app.services.errors import NotFound
from app.store.base import Store, one

OPTIONS = {
    "submit_anyway": {
        "available": True,
        "text": "You can submit now even if something is listed here. An officer will look at your application either way.",
    },
    "ask_a_person": {
        "available": True,
        "text": "You can ask a person for help, or ask for your application to be assessed by a person without AI tools.",
    },
}


def precheck(
    store: Store,
    actor: Actor,
    *,
    grant_program_id: str,
    fields: dict[str, Any],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    require_role(actor, "applicant", "officer", "admin")
    program = one(store.select("grant_programs", eq={"id": grant_program_id}, limit=1))
    if not program or not program.get("active", True):
        raise NotFound("Grant program not found")
    pack = load_active_pack_for_program(store, grant_program_id)

    required_fields: dict[str, list[str]] = {}
    # document type -> (how many are needed, which rules need it)
    required_docs: dict[str, tuple[int, list[str]]] = {}
    for rule in pack.rules:
        for f in rule.params.get("required_fields", []):
            required_fields.setdefault(f, []).append(rule.rule_code)
        wanted: dict[str, int] = {}
        for d in rule.params.get("required_documents", []):
            wanted[d] = wanted.get(d, 0) + 1
        for d, n in wanted.items():
            count, codes = required_docs.get(d, (0, []))
            required_docs[d] = (max(count, n), codes + [rule.rule_code])

    missing_fields = [
        {"field": f, "label": f.replace("_", " "), "rules": codes}
        for f, codes in sorted(required_fields.items())
        if fields.get(f) in (None, "") or (isinstance(fields.get(f), str) and not fields[f].strip())
    ]

    wrong_type = []
    detected_counts: dict[str, int] = {}
    for i, doc in enumerate(documents):
        declared = normalise_declared(doc.get("declared_type") or "")
        detected = detect_type(doc.get("text") or doc.get("extracted_text") or "", declared)
        detected_counts[detected] = detected_counts.get(detected, 0) + 1
        if declared != detected:
            wrong_type.append(
                {
                    "index": i,
                    "file_name": doc.get("file_name"),
                    "declared_as": DOC_LABEL_SHORT.get(declared, declared),
                    "looks_like": DOC_LABEL_SHORT.get(detected, detected),
                    "message": f"This file was uploaded as {DOC_LABEL_SHORT.get(declared, declared)} "
                    f"but looks like {DOC_LABEL_SHORT.get(detected, detected)}. Please check you chose the right file.",
                }
            )

    missing_documents = [
        {"document_type": d, "label": DOC_LABEL_SHORT.get(d, d), "rules": codes,
         "needed": n, "provided": detected_counts.get(d, 0)}
        for d, (n, codes) in sorted(required_docs.items())
        if detected_counts.get(d, 0) < n
    ]

    return {
        "grant_program_id": grant_program_id,
        "rule_pack_version": pack.version,
        "missing_fields": missing_fields,
        "missing_documents": missing_documents,
        "wrong_document_type": wrong_type,
        "items_to_check": len(missing_fields) + len(missing_documents) + len(wrong_type),
        "note": "This check only looks for missing items. It does not decide whether you are eligible.",
        "options": OPTIONS,
    }
