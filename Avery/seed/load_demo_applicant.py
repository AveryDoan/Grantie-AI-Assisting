"""Load one fictional applicant's PDF files through the real applicant upload flow.

    python -m seed.load_demo_applicant --dir seed/demo_sita                       # into the running demo API (http://localhost:8010)
    python -m seed.load_demo_applicant --dir seed/demo_sita --api http://host:8010

It does what the applicant screen does: sign in as the demo applicant "sita", create a draft for the Study NT scholarship
program, upload each file to POST /me/applications/{id}/documents, then submit. Nothing is hard-coded about the results: the
form answers are read from the "Application Responses" PDF by label, each other file is put in a document slot by its file
name and its text, and everything after that is the real parsing, redaction, rule engine and consistency code.

The files are copied into seed/demo_sita/ and never edited. A slot with no file stays "Not provided".
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path
from typing import Any

# What the form asks, so an answer can be filed under the right key. Matched on the heading line of the "Application Responses" PDF.
QUESTIONS: list[tuple[str, str]] = [
    (r"what are you studying now", "current_study"),
    (r"^academic achievements", "academic_achievements"),
    (r"^leadership", "leadership"),
    (r"^community engagement", "community_engagement"),
    (r"how will studying in the nt contribute", "nt_contribution"),
]
HEADER_LABELS = {"applicant": "applicant_name", "date of birth": "date_of_birth", "email": "email", "mobile": "phone", "phone": "phone"}
FOOTER = re.compile(r"^SAMPLE DOCUMENT", re.I)

# File name hints -> the type the applicant screen would use for that slot.
NAME_HINTS: list[tuple[str, str]] = [
    ("application_responses", "application responses"), ("confirmation_of_enrolment", "coe"), ("coe", "coe"),
    ("flight", "travel booking"), ("itinerary", "travel booking"), ("booking", "travel booking"),
    ("letter_of_support", "referee letter"), ("reference", "referee letter"), ("biography", "biography"),
    ("resume", "resume"), ("cv", "resume"), ("certificate", "certificate"), ("award", "certificate"),
    ("headshot", "headshot"), ("photo", "headshot"),
]


def pdf_pages(data: bytes) -> list[str]:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def parse_form(text: str) -> dict[str, dict[str, str]]:
    """{'fields': {...}, 'answers': {...}} from the "Application Responses" PDF text. Only what the PDF says."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not FOOTER.match(l.strip())]
    fields: dict[str, str] = {}
    answers: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        key = next((k for pat, k in QUESTIONS if re.search(pat, line, re.I) and len(line) < 120), None)
        if key:
            current = key
            answers.setdefault(key, [])
            continue
        if current is None:
            for part in re.split(r"\s+\|\s+", line):
                label, sep, value = part.partition(":")
                k = HEADER_LABELS.get(label.strip().lower())
                if sep and k and value.strip():
                    fields[k] = value.strip()
            continue
        answers[current].append(line)
    out_answers = {k: " ".join(v).strip() for k, v in answers.items() if v}
    if "date_of_birth" in fields:
        from app.pipeline.parsing import parse_date

        d = parse_date(fields["date_of_birth"])
        if d:
            fields["date_of_birth"] = d.isoformat()
    return {"fields": fields, "answers": out_answers}


def declared_type(name: str, text: str) -> str:
    low = name.lower()
    for hint, kind in NAME_HINTS:
        if hint in low:
            return kind
    from app.pipeline.documents import classify

    return {"coe": "coe", "travel_booking": "travel booking", "referee_letter": "referee letter"}.get(classify(text), "other")


def read_files(directory: Path) -> list[dict[str, Any]]:
    """Each PDF in the folder with a quick check that it has a usable text layer (no OCR is ever attempted)."""
    out = []
    for path in sorted(directory.glob("*.pdf")):
        data = path.read_bytes()
        pages = pdf_pages(data)
        text = "\n".join(pages)
        words = len(text.split())
        out.append({"path": path, "name": path.name, "data": data, "text": text, "pages": len(pages), "words": words,
                    "text_layer": all(len(re.findall(r"\w", p)) >= 20 for p in pages), "declared": declared_type(path.name, text)})
    return out


def load(client: Any, directory: Path, *, program_name_contains: str = "Study NT") -> dict[str, Any]:
    """Create the draft, upload each file, submit. `client` is an httpx-style client already carrying the applicant's token."""
    files = read_files(directory)
    if not files:
        raise SystemExit(f"No PDF files in {directory}")
    form_file = next((f for f in files if f["declared"] == "application responses"), None)
    form = parse_form(form_file["text"]) if form_file else {"fields": {}, "answers": {}}
    programs = client.get("/programs").json()
    program = next((p for p in programs if program_name_contains.lower() in p["name"].lower()), None)
    if not program:
        raise SystemExit("The Study NT scholarship program was not found")
    draft = client.post("/me/applications", json={"grant_program_id": program["id"], **form})
    draft.raise_for_status()
    app_id = draft.json()["id"]
    uploads = []
    for f in files:
        r = client.post(f"/me/applications/{app_id}/documents", json={
            "file_name": f["name"], "declared_type": f["declared"], "content_base64": base64.b64encode(f["data"]).decode()})
        r.raise_for_status()
        up = r.json()
        uploads.append({"file": f["name"], "declared_type": f["declared"], "pages": f["pages"], "words": f["words"],
                        "text_layer": f["text_layer"], "extraction_status": up["extraction_status"], "id": up["id"]})
    submitted = client.post(f"/me/applications/{app_id}/submit", json={})
    submitted.raise_for_status()
    return {"application_id": app_id, "reference": submitted.json()["reference"], "program": program["name"], "form": form, "uploads": uploads}


def main() -> None:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path("seed/demo_sita"))
    ap.add_argument("--api", default="http://localhost:8010")
    ap.add_argument("--role", default="sita", help="demo applicant login to use")
    args = ap.parse_args()
    with httpx.Client(base_url=args.api, timeout=120) as c:
        login = c.post("/demo/login", json={"role": args.role})
        if login.status_code != 200:
            raise SystemExit(f"Could not sign in as the demo applicant ({login.status_code}). Is the API running in demo mode?")
        c.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        result = load(c, args.dir)
    print(f"Application {result['reference']} for {result['program']}")
    print(f"Form read from the PDF: {len(result['form']['fields'])} fields, {len(result['form']['answers'])} answers")
    print(f"{'File':<38}{'Slot type':<22}{'Pages':>5}{'Words':>7}  Text layer")
    for u in result["uploads"]:
        print(f"{u['file']:<38}{u['declared_type']:<22}{u['pages']:>5}{u['words']:>7}  {'yes' if u['text_layer'] and u['extraction_status'] == 'ok' else 'NO'}")
    if not all(u["text_layer"] and u["extraction_status"] == "ok" for u in result["uploads"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
