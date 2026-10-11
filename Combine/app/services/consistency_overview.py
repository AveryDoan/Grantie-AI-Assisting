"""Consistency of information: one row per comparison, including the ones that agree.

Each row says what was compared in plain words, the result (Consistent / Differs / Cannot compare) and the officer's decision.
Rows come from two places: value-by-value comparisons of the form against each document (plain code, worked out here from
data already stored), and the consistency flags (cross-document, timeline, statements, file details).

- A difference is a difference to check, never a verdict. Results never change a rule result and never block sign-off.
- Both values are shown side by side, each with its source and, where the exact position is known, where it sits in the
  original text (so the screen can highlight it). A position is never guessed.
- Links to other applications are NOT here: they have their own tab.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.documents import compare_value
from app.pipeline.parsing import parse_date

NAME_THRESHOLD = 85.0

# (typed form field, document type, field read from the document, comparison kind, plain words)
PAIRS: list[tuple[str, str, str, str, str]] = [
    ("applicant_name", "coe", "full_name", "name", "Name on the form vs name on the CoE"),
    ("applicant_name", "travel_document", "full_name", "name", "Name on the form vs name on the passport"),
    ("applicant_name", "visa", "full_name", "name", "Name on the form vs name on the visa"),
    ("date_of_birth", "travel_document", "date_of_birth", "date", "Date of birth on the form vs on the passport"),
    ("passport_number", "travel_document", "passport_number", "id", "Passport number on the form vs on the passport"),
    ("education_provider", "coe", "provider_name", "text", "Provider on the form vs provider on the CoE"),
    ("course_name", "coe", "course_name", "text", "Course on the form vs course on the CoE"),
    ("course_start_date", "coe", "course_start_date", "date", "Course start date vs CoE start date"),
    ("arrival_date", "travel_booking", "arrival_date", "date", "Arrival date on the form vs arrival date on the booking"),
]
DOC_NAME = {"coe": "CoE", "travel_document": "passport", "visa": "visa", "travel_booking": "booking"}

# Families of checks the flags come from: the plain words for the row when there is no difference to show.
FAMILIES: dict[str, str] = {
    "cross_document.arrival_vs_start": "Arrival date vs course start date",
    "cross_document.coe_length": "Course length on the CoE vs the typed dates",
    "cross_document.course": "Course on the form vs course on the documents",
    "cross_document.provider": "Provider on the form vs provider on the documents",
    "cross_document.letter_subject": "Name the letter is about vs name on the form",
    "cross_document.claim_needs_evidence": "Awards the application claims vs the certificates provided",
    "cross_document.known_since_vs_degree_start": "When the referee met the applicant vs when the degree started",
    "cross_document.known_for_vs_timeline": "How long the referee has known the applicant vs the dates in the letter",
    "cross_document.referee_date_future": "Date on the referee letter vs the date of this application",
    "cross_document.referee_date_old": "Date on the referee letter vs the allowed period",
    "cross_document.referee_date_window": "Date on the referee letter vs the allowed period",
    "timeline.dates_backwards": "Order of the dates in the timeline",
    "timeline.overlapping_full_time": "Full-time activities in the same months",
    "timeline.role_before_age": "Start of a role vs the applicant's age at the time",
    "timeline.visa_before_coe": "Visa date vs the date the CoE was issued",
    "narrative.conflicting_statements": "Two statements in the application",
    "narrative.unverified": "Two statements in the application (not verified)",
    "document_integrity.author_is_applicant": "Who made the file vs the applicant",
    "document_integrity.created_after_dated": "When the file was made vs the date it shows",
    "document_integrity.created_before_dated": "When the file was made vs the date it shows",
    "document_integrity.editing_software": "How the file was saved",
    "document_integrity.modified_after_created": "When the file was changed vs when it was made",
}
PREFIX_FAMILIES = {"cross_document.typed_": "Details typed on the form vs the CoE"}


def family_label(check_id: str, fallback: str) -> str:
    return FAMILIES.get(check_id) or next((v for k, v in PREFIX_FAMILIES.items() if check_id.startswith(k)), fallback)


# One summary row per family when it ran and found nothing: (title, check ids it covers, trace key or None)
SUMMARY_ROWS: list[tuple[str, tuple[str, ...], str | None]] = [
    ("Arrival date vs course start date", ("cross_document.arrival_vs_start",), None),
    ("Dates and roles in the timeline", ("timeline.",), "timeline"),
    ("Statements across the form and documents", ("narrative.",), "narrative"),
    ("Referee letter dates vs the allowed period", ("cross_document.referee_date",), None),
    ("How long referees say they have known the applicant vs their letters' dates", ("cross_document.known_for",), None),
    ("File details vs the dates the documents show", ("document_integrity.",), None),
]
CATEGORY = {"cross_document": "Across documents", "timeline": "Timeline", "narrative": "Statements", "document_integrity": "File details"}
DECISION = {"open": "Not decided", "confirmed": "Needs follow-up", "dismissed": "Dismissed"}
RESULT_RANK = {"Needs evidence": 0, "Differs": 1, "Cannot compare": 2, "Consistent": 3}


def _form_span(form_text: str, key: str | None, value: str | None) -> tuple[int, int] | None:
    if not key or not value:
        return None
    m = re.search(rf"^{re.escape(key)}: ", form_text, re.M)
    if not m:
        return None
    start = m.end()
    nl = form_text.find("\n", start)
    end = nl if nl >= 0 else len(form_text)
    at = form_text.find(value, start, end)
    return (at, at + len(value)) if at >= 0 else (start, end)


def _side(label: str, value: str | None, source: str, source_label: str, text: str, span: tuple[int, int] | None) -> dict[str, Any]:
    return {"label": label, "value": value, "source": source, "source_label": source_label,
            "start": span[0] if span else None, "end": span[1] if span else None, "has_text": bool(text)}


def _doc_span(text: str, value: str | None) -> tuple[int, int] | None:
    at = text.find(value) if value and text else -1
    return (at, at + len(value)) if at >= 0 else None


def pair_rows(app: dict[str, Any], documents: list[dict[str, Any]], texts: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    fields = {**((app.get("application_text") or {}).get("fields") or {}), **((app.get("application_text") or {}).get("answers") or {})}
    form_text = (texts.get("application_text") or {}).get("text", "")
    rows = []
    for key, doc_type, doc_field, kind, words in PAIRS:
        doc = next((d for d in documents if d.get("detected_type") == doc_type or (not d.get("detected_type") and d.get("declared_type") == doc_type)), None)
        typed = str(fields.get(key) or "").strip() or None
        name = DOC_NAME[doc_type]
        left = _side("On the form", typed, "application_text", "Application form", form_text, _form_span(form_text, key, typed))
        if doc is None:
            if doc_type != "coe" and typed is None:
                continue   # nothing was asked for and nothing was uploaded
            rows.append({"id": f"pair:{key}:{doc_type}", "check": "Form vs documents", "compared": words, "result": "Cannot compare",
                         "why": f"No {name} was uploaded", "decision": "Not needed", "left": left, "right": None, "flag_ids": []})
            continue
        dkey = f"document:{doc['id']}"
        dtext = (texts.get(dkey) or {}).get("text") or doc.get("extracted_text") or ""
        value = (doc.get("extracted_fields") or {}).get(doc_field)
        right = _side(f"On the {name}", value, dkey, f"{name.upper() if name == 'CoE' else name.capitalize()} · {doc.get('file_name')}", dtext, _doc_span(dtext, value))
        if typed is None or not value:
            result, why = "Cannot compare", ("Not provided on the form" if typed is None else f"Not found on the {name}")
        else:
            outcome, _score = compare_value(kind, typed, str(value), NAME_THRESHOLD)
            result = {"match": "Consistent", "unparseable": "Cannot compare"}.get(outcome, "Differs")
            why = {"variant": "Written differently", "unparseable": f"The {name} value could not be read"}.get(outcome)
        rows.append({"id": f"pair:{key}:{doc_type}", "check": "Form vs documents", "compared": words, "result": result, "why": why,
                     "decision": "Not needed", "left": left, "right": right, "flag_ids": []})
    return rows


def _flag_side(e: dict[str, Any], restorer: Any, form_text: str, doc_texts: dict[str, str]) -> dict[str, Any]:
    src = e.get("source") or "form"
    if e["kind"] == "quote":
        hit = restorer.locate(e.get("quote"), None if src in ("form", "application_text") else src)
        source = hit["source"] if hit else ("application_text" if src in ("form", "application_text") else src)
        return {"label": e["label"], "value": (hit or {}).get("text") or restorer.original(e.get("quote"), None if src == "form" else src),
                "source": source, "source_label": None, "start": hit["start"] if hit else None, "end": hit["end"] if hit else None,
                "has_text": bool(hit)}
    span = _form_span(form_text, e.get("field"), e.get("value"))
    return {"label": e["label"], "value": e.get("value"), "source": "application_text", "source_label": "Application form",
            "start": span[0] if span else None, "end": span[1] if span else None, "has_text": bool(form_text)}


def flag_rows(flags: list[dict[str, Any]], restorer: Any, texts: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    form_text = (texts.get("application_text") or {}).get("text", "")
    rows = []
    for f in flags:
        if f["check_type"] == "cross_application":
            continue
        evidence = [e for e in (f.get("evidence") or []) if e["kind"] in ("quote", "field")]
        sides = [_flag_side(e, restorer, form_text, {}) for e in evidence[:2]]
        for s in sides:
            s["source_label"] = s["source_label"] or (texts.get(s["source"]) or {}).get("label") or "Document"
        verified = f["verification"] == "verified"
        rows.append({
            "id": f"flag:{f['id']}", "check": CATEGORY.get(f["check_type"], f["type_label"]), "compared": family_label(f["check_id"], f["type_label"]),
            "result": ("Needs evidence" if f["check_id"] == "cross_document.claim_needs_evidence" else "Differs") if verified else "Cannot compare",
            "why": f["description"] if verified else "The AI pointed at a possible difference, but code could not find its passages",
            "decision": DECISION[f["status"]] if verified else "Not decided", "left": sides[0] if sides else None,
            "right": sides[1] if len(sides) > 1 else None, "flag_ids": [f["id"]], "family": f["check_id"],
        })
    return rows


def summary_rows(flags: list[dict[str, Any]], trace: dict[str, Any], app: dict[str, Any], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A row for each family that found nothing: Consistent when it ran, Cannot compare when it could not."""
    ids = [f["check_id"] for f in flags]
    fields = (app.get("application_text") or {}).get("fields") or {}
    out = []
    for title, prefixes, trace_key in SUMMARY_ROWS:
        if any(i.startswith(p) for i in ids for p in prefixes):
            continue
        t = (trace.get(trace_key) or {}) if trace_key else {}
        if trace_key and (t.get("error") or t.get("skipped") or not t):
            result, why = "Cannot compare", (t.get("skipped") or ("The AI check did not answer" if t.get("error") else "Not checked yet"))
        elif prefixes[0].startswith("cross_document.arrival"):
            def got(doc_type: str, key: str) -> bool:
                return any(d.get("detected_type") == doc_type and (d.get("extracted_fields") or {}).get(f"{key}_iso") for d in documents)

            ok = (parse_date(str(fields.get("arrival_date") or "")) or got("travel_booking", "arrival_date")) and (
                parse_date(str(fields.get("course_start_date") or "")) or got("coe", "course_start_date"))
            result, why = ("Consistent", None) if ok else ("Cannot compare", "A date is missing")
        elif prefixes[0].startswith("cross_document.referee") or prefixes[0].startswith("cross_document.known_for"):
            ok = any(d.get("detected_type") == "referee_letter" for d in documents)
            result, why = ("Consistent", None) if ok else ("Cannot compare", "No referee letter was uploaded")
        elif prefixes[0].startswith("document_integrity"):
            ok = bool(documents)
            result, why = ("Consistent", None) if ok else ("Cannot compare", "No documents were uploaded")
        else:
            result, why = "Consistent", None
        out.append({"id": f"ok:{title}", "check": CATEGORY.get({"timeline.": "timeline", "narrative.": "narrative", "document_integrity.": "document_integrity"}.get(prefixes[0], "cross_document"), "Across documents"), "compared": title, "result": result, "why": why, "decision": "Not needed",
                    "left": None, "right": None, "flag_ids": []})
    return out


def build(app: dict[str, Any], documents: list[dict[str, Any]], flags: list[dict[str, Any]], trace: dict[str, Any],
          restorer: Any, texts: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = pair_rows(app, documents, texts) + flag_rows(flags, restorer, texts) + summary_rows(flags, trace, app, documents)
    rows.sort(key=lambda r: (RESULT_RANK[r["result"]], r["decision"] != "Not decided"))
    return rows
