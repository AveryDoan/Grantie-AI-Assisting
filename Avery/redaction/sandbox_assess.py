"""Sandbox: build one application from a folder of documents and show what the AI reads and what comes out.

    python -m redaction.sandbox assess <folder> --known "Full Name" 2003/03/14 [--answers <folder>] [--llm stub|gemini]

Local by default: the offline keyword stub answers the AI-assisted rules (it is NOT an LLM and can be
wrong), the network is blocked, and nothing goes to Supabase. --llm gemini sends the REDACTED text (never
the original) to Gemini through the same guard the app uses; it needs GEMINI_API_KEY in .env and is
never the default.

Folder convention (file names, any order):
  *Application_Responses*  the applicant's written answers (headings: study now, achievements, leadership,
                           community, "How will studying in the NT contribute")
  *Confirmation* / *CoE*   Confirmation of Enrolment        *Flight* / *Booking*  arrival evidence
  *Letter_of_Support*      referee letters                  *Biography*           biography text
  *.jpg / *.jpeg / *.png   headshot                         anything else         other supporting document
Written answers that are not in the responses file are read from <answers>/<field>.txt
(for example community_engagement.txt).

Report (officer only, contains personal information): app_report.html. The terminal shows counts, tokens
and statuses only.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from redaction import sandbox as sb

HEADINGS = {
    "current_study": ("what are you studying now",),
    "academic_achievements": ("academic achievements",),
    "leadership": ("leadership",),
    "community_engagement": ("community engagement",),
    "nt_contribution": ("how will studying in the nt",),
}
SUPPLIED_IDS = [
    (re.compile(r"(?i)\bCoE\)?\s*(?:number|no\.?|code)\s*:?\s*([A-Z0-9-]{6,})"), "CoE number"),
    (re.compile(r"(?i)\bstudent\s*(?:id|no\.?|number)\s*:?\s*([A-Z0-9-]{6,})"), "student ID"),
]
STATUS_CLASS = {"Met": "ok", "Not met": "bad", "Needs evidence": "warn", "Unclear": "unclear", "Evidence only": "judge"}


def declared_type(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ("confirmation", "coe")):
        return "coe"
    if any(w in n for w in ("flight", "booking", "itinerary")):
        return "travel booking"
    if "letter_of_support" in n or "referee" in n or "letter of support" in n:
        return "referee letter"
    if n.endswith((".jpg", ".jpeg", ".png")):
        return "headshot"
    return "other"


def parse_responses(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """(fields, answers) from an application-responses document."""
    fields: dict[str, str] = {}
    m = re.search(r"(?im)^\s*Mobile\s*:?\s*(\+?[\d ()-]{7,})\s*$", text)
    if m:
        fields["phone"] = m.group(1).strip()
    answers: dict[str, str] = {}
    current, buf = None, []
    for line in text.splitlines():
        low = line.strip().lower()
        hit = next((k for k, hs in HEADINGS.items() if any(low.startswith(h) for h in hs)), None)
        if hit and (len(line.strip()) < 120):
            if current:
                answers[current] = " ".join(buf).strip()
            current, buf = hit, []
        elif low.startswith("sample document"):
            break
        elif current:
            buf.append(line.strip())
    if current:
        answers[current] = " ".join(buf).strip()
    return fields, {k: v for k, v in answers.items() if v}


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def assess_folder(folder: Path, known: list[str], *, answers_dir: Path | None = None, llm: str = "stub",
                  not_personal: list[str] | None = None, allow_cloud_sync: bool = False,
                  out_root: Path | None = None, echo=print) -> Path:
    from app.config import Settings
    from app.llm import LLMClient
    from app.llm.stub import OfflineStubProvider
    from app.pipeline.documents import classify, extract_fields, normalise_declared
    from app.pipeline.orchestrator import assess_application, load_reference_lists
    from app.pipeline.rules_loader import load_rule_pack
    from app.store.base import one
    from app.store.memory import MemoryStore
    from redaction.config import default_config
    from redaction.documents import document_source
    from redaction.extract import extract_document
    from redaction.passport import parse_passport
    from redaction.pipeline import GuardedLLM, run_redaction
    from redaction.recognizers import KnownValue, parse_any_date
    from redaction.restore import restore
    from redaction.spans import quote_to_original
    from redaction.storage import token_map_rows
    from redaction.structured import APPLICATION_SOURCE, original_application_text
    from seed import data
    from seed.run import seed

    folder = folder.expanduser()
    out_root = (out_root or sb.data_dir() / "output").expanduser()
    if not folder.is_dir():
        raise sb.SandboxError("That folder does not exist.")
    sb.guard_location(folder, "input folder", allow_cloud_sync)
    sb.guard_location(out_root, "output folder", allow_cloud_sync)
    tc = sb.cipher()
    sb.quiet_libraries()
    cfg = default_config()
    not_personal = [w.strip() for w in (not_personal or []) if w.strip()]
    if not_personal:
        # An officer's call for THIS run only: ordinary words the name detector mistook for people
        # (the config's allowlist mechanism). Nothing in config/ or the detector is changed.
        cfg = cfg.model_copy(update={"allowlist": [*cfg.allowlist, *(f"re:(?-i:\\b{re.escape(w)}\\b)" for w in not_personal)]})
    today = date.today()
    fields_known, extra_known = sb.known_inputs(known)
    name = fields_known.get("applicant_name")
    dob = parse_any_date(fields_known.get("date_of_birth", ""))
    if not name or not dob:
        raise sb.SandboxError('Give the applicant\'s name and date of birth: --known "Full Name" 2003/03/14')

    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    if not files:
        raise sb.SandboxError("The folder has no files.")
    source_of: dict[str, str] = {}      # field -> where it came from (never the value)
    log: list[str] = []                  # what happened to each file (no values, no file names)

    with sb.no_network():
        # ---- 1. read every file locally (text layer, OCR only for images/scans)
        responses_fields, answers = {}, {}
        docs: list[dict[str, Any]] = []
        extracted: dict[str, Any] = {}
        bio_text = None
        for i, f in enumerate(files, 1):
            try:
                data_b = f.read_bytes()
            except OSError:  # e.g. a macOS permission tag: record it as a file a person must open, never skip it silently
                data_b = b""
                log.append(f"file {i}: could not be read by this program (permission); a person must open it")
            doc_id = "doc-" + (hashlib.sha256(data_b).hexdigest()[:10] if data_b else hashlib.sha256(f.name.encode()).hexdigest()[:10])
            ext = extract_document(doc_id, f.name, data_b)
            if ext.file_kind.startswith("image") or (ext.file_kind == "pdf" and ext.status == "no_text"):
                from redaction.ocr import available, ocr_document

                if available():
                    ext = ocr_document(doc_id, f.name, data_b, "pdf" if ext.file_kind == "pdf" else "image").document
                else:
                    log.append(f"file {i}: no text layer and local OCR is not installed")
            n = f.name.lower()
            if "application_responses" in n or "responses" in n:
                rf, ra = parse_responses(ext.text)
                responses_fields.update(rf)
                answers.update(ra)
                log.append(f"file {i}: written answers ({len(ra)} found)")
                continue
            if "biograph" in n:
                bio_text = "\n".join(p.text for p in ext.pages).strip()
                bio_text = re.sub(r"(?m)^\[page \d+\]\n?", "", bio_text)
                bio_text = "\n".join(l for l in bio_text.splitlines() if not l.lower().startswith("sample document")).strip()
                bio_text = re.sub(r"^Biography\s*\n", "", bio_text)
                log.append(f"file {i}: biography text")
                continue
            kind = declared_type(f.name)
            docs.append({"id": doc_id, "file_name": f"document-{len(docs) + 1}", "declared_type": kind,
                         "extracted_text": ext.text if ext.status == "ok" else "", "is_sample": True, "storage_path": ""})
            extracted[doc_id] = ext
            log.append(f"file {i}: {kind} ({ext.status}{', needs a person' if ext.needs_manual_review else ''})")

        # ---- 2. written answers: from the responses file, then <answers>/<field>.txt
        for field_name in HEADINGS:
            if field_name in answers:
                source_of[field_name] = "your application-responses file"
            elif answers_dir and (answers_dir / f"{field_name}.txt").is_file():
                answers[field_name] = (answers_dir / f"{field_name}.txt").read_text(encoding="utf-8").strip()
                source_of[field_name] = "written for the sample (not in your documents)"
        if bio_text:
            answers["biography"] = bio_text
            source_of["biography"] = "your biography file"

        # ---- 3. form fields from the documents
        coe = next((d for d in docs if d["declared_type"] == "coe" and d["extracted_text"]), None)
        cf = extract_fields(coe["extracted_text"]) if coe else {}
        fields: dict[str, str] = {"applicant_name": name, "date_of_birth": dob.isoformat(), "email": "", **responses_fields}
        source_of["applicant_name"] = source_of["date_of_birth"] = "you supplied (--known)"
        if "phone" in responses_fields:
            source_of["phone"] = "your application-responses file"
        resume = next((d for d in docs if "resume" in extracted[d["id"]].file_name.lower() or "cv" in extracted[d["id"]].file_name.lower()), None)
        if resume:
            head = "\n".join(resume["extracted_text"].splitlines()[:8])
            m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", head)
            if m:
                fields["email"] = m.group(0)
                source_of["email"] = "your resume (header)"
        fields = {k: v for k, v in fields.items() if v}
        if coe:
            ct = coe["extracted_text"]
            m = re.search(r"(?im)^\s*Residential address\s*(?:\([^)\n]*\))?\s*:?\s+(.+)$", ct)
            if m:
                fields["residential_address"] = m.group(1).strip()
                fields["residential_country"] = m.group(1).rsplit(",", 1)[-1].strip()
                fields["postal_address"], fields["postal_country"] = fields["residential_address"], fields["residential_country"]
                source_of["residential_address"] = source_of["residential_country"] = "your Confirmation of Enrolment"
            m = re.search(r"(?im)^\s*Country of citizenship[:\s]+(\S.*)$", ct)
            if m:
                fields["nationality"] = m.group(1).strip()
                source_of["nationality"] = "your Confirmation of Enrolment"
                fields["australian_or_nz_citizen_or_pr"] = "No" if m.group(1).strip().lower() not in ("australia", "new zealand") else "Yes"
                source_of["australian_or_nz_citizen_or_pr"] = "derived from the citizenship on the Confirmation of Enrolment"
            for key, fname in (("education_provider", "provider_name"), ("course_name", "course_name"),
                               ("course_start_date", "course_start_date_iso"), ("study_load", "study_load")):
                if cf.get(fname):
                    fields[key] = cf[fname]
                    source_of[key] = "your Confirmation of Enrolment"
        booking = next((d for d in docs if d["declared_type"] == "travel booking" and d["extracted_text"]), None)
        if booking:
            bf = extract_fields(booking["extracted_text"])
            if bf.get("arrival_date_iso"):
                fields["arrival_date"] = bf["arrival_date_iso"]
                source_of["arrival_date"] = "your flight booking"
        fields["under_18"] = "Yes" if _age(dob, today) < 18 else "No"
        source_of["under_18"] = "derived from the date of birth"
        fields.update({"declaration_agreed": "Yes", "declaration_name": name, "declaration_date": today.isoformat()})
        for k in ("declaration_agreed", "declaration_name", "declaration_date"):
            source_of[k] = "added for the sample (a declaration is not a document)"

        # ---- 4. identifiers printed on the documents, supplied as known values (like form fields)
        id_values: list[tuple[str, str]] = []
        for d in docs:
            for rx, label in SUPPLIED_IDS:
                for m in rx.finditer(d["extracted_text"]):
                    if all(m.group(1) != v for v, _ in id_values):
                        id_values.append((m.group(1), label))
        supplied = [KnownValue(v, "ID", "supplied") for v, _ in id_values]
        passport = [k for d in docs for k in parse_passport(d["extracted_text"]).known]

        # ---- 5. the application, the pack, the redaction (twice: without and with the supplied IDs)
        store = MemoryStore()
        seed(store)
        base = store.select("applications", eq={"id": data.sid("application:N01")})[0]
        app = {**base, "id": "app-sandbox", "applicant_id": base["applicant_id"], "status": "submitted",
               "submitted_at": datetime.now(timezone.utc).isoformat(),
               "application_text": {"fields": fields, "answers": answers}, "manual_assessment_requested": False}
        pack = load_rule_pack(store, app["rule_pack_id"])
        ext_list = [extracted[d["id"]] for d in docs]
        kinds = {d["id"]: ("referee_letter" if classify(extracted[d["id"]].text) == "referee_letter" else "document") for d in docs}

        def redact(extra):
            return run_redaction("app-sandbox", app["application_text"], ext_list, document_kinds=kinds, cfg=cfg,
                                 extra_known=extra_known + passport + extra)

        blocked_run = redact([])
        outcome = redact(supplied)
        tm = outcome.token_map

        # ---- 6. the assessment (offline stub by default)
        settings = Settings(_env_file=None, consistency_check=False)
        if llm == "gemini":
            from app.llm import build_llm_client

            client = build_llm_client(settings.model_copy(update={"llm_provider": "gemini"}))
            label = "Gemini (sent REDACTED text only)"
        else:
            client = LLMClient(OfflineStubProvider(), temperature=0.0)
            label = "offline keyword stub (NOT an LLM; can be wrong)"
        result = None
        if outcome.llm_allowed:
            result = assess_application(
                application=app, documents=docs, pack=pack, register_rows=[], llm=client, settings=settings,
                applicant=one(store.select("applicants", eq={"id": app["applicant_id"]})),
                reference_lists=load_reference_lists(store, pack), redaction=outcome)

    # ---- 7. restore AI quotes to the applicant's words (officer view)
    app_original = original_application_text(app["application_text"])
    texts = {APPLICATION_SOURCE: (outcome.redacted_text, app_original)}
    for rd in outcome.documents:
        if rd.included_in_ai_input:
            texts[document_source(rd.document_id)] = (rd.redacted_text, extracted[rd.document_id].text)

    def original(quote: str | None, source: str | None) -> str | None:
        if not quote:
            return None
        for s in ([source] if source in texts else list(texts)):
            red, orig = texts[s]
            m = quote_to_original(quote, red, orig, tm, s)
            if m is not None:
                return m.original
        return restore(quote, tm)

    rules = {r.id: r for r in pack.rules}
    rows = []
    for f in (result.findings if result else []):
        r = rules[f.rule_id]
        quotes = []
        if f.evidence_quote:
            quotes.append((f.evidence_quote, "application_text", f.quote_verified))
        for q in f.supporting_quotes or []:
            quotes.append((q.get("quote"), q.get("source") or "application_text", bool(q.get("verified"))))
        rows.append({"code": f.rule_code, "text": r.rule_text, "section": (r.params or {}).get("section"), "method": f.check_source,
                     "status": f.ai_status, "why": f.rationale, "confidence": f.confidence, "clarify": f.needs_applicant_clarification,
                     "quotes": [{"ai": q, "restored": original(q, s), "verified": v, "where": ("form" if s == APPLICATION_SOURCE else "document")}
                                for q, s, v in quotes if q]})
    facts = []
    if result:
        for fact in result.facts.facts.values():
            facts.append({"key": fact.fact_key, "value": fact.fact_value, "quote": fact.source_quote, "verified": fact.quote_verified,
                          "restored": original(fact.source_quote, fact.source)})

    doc_states = []
    for d in docs:
        ext = extracted[d["id"]]
        rd = next(x for x in outcome.documents if x.document_id == d["id"])
        doc_states.append({"n": len(doc_states) + 1, "kind": d["declared_type"], "pages": len(ext.pages),
                           "state": "read by the AI" if rd.included_in_ai_input else "NOT sent: " + ("needs a person to read it" if rd.needs_manual_review else "no readable text"),
                           "source": document_source(d["id"]) if rd.included_in_ai_input else None, "redacted": rd.redacted_text})
    checks = result.document_checks.checks if result else []

    # ---- write
    app_id = "app-" + hashlib.sha256("|".join(sorted(p.name + str(p.stat().st_size) for p in files)).encode()).hexdigest()[:10]
    if not_personal:
        app_id += "-reviewed"
    out = out_root / app_id
    out.mkdir(parents=True, exist_ok=True)
    rows_enc = token_map_rows(app_id, None, tm, tc)
    (out / "token_map.enc").write_text(json.dumps({"format": sb.TOKEN_FILE_FORMAT, "doc_id": app_id,
                                                   "blob": tc.encrypt(app_id, "token_map", json.dumps(rows_enc, ensure_ascii=False))}), encoding="utf-8")
    summary = {"app_id": app_id, "llm": label, "marked_not_personal": not_personal, "ai_status": outcome.ai_status, "counts": tm.counts(), "leak_without_ids": blocked_run.leak_scan.summary(),
               "leak_with_ids": outcome.leak_scan.summary(), "location_class": outcome.location_class,
               "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in ("Met", "Not met", "Needs evidence", "Unclear", "Evidence only")}}
    (out / "meta.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "app_report.html").write_text(report(app_id, label, fields, answers, source_of, outcome, blocked_run, id_values, doc_states,
                                                facts, rows, checks, result, log, not_personal), encoding="utf-8")
    sb.write_index(out_root)

    echo(f"Application {app_id}: {len(docs)} document(s) + written answers; AI = {label}")
    echo(f"  Redaction: {', '.join(f'{k} {v}' for k, v in tm.counts().items())}; location class: {outcome.location_class}")
    if not_personal:
        echo(f"  Officer marked as not personal (this run only): {', '.join(not_personal)}")
    echo(f"  Leak scan without the supplied IDs: {'PASS' if not blocked_run.leak_scan.summary() else 'FAIL ' + str(blocked_run.leak_scan.summary()) + ' - the AI would be blocked'}")
    echo(f"  Leak scan with {len(id_values)} supplied ID(s): {'PASS' if outcome.leak_scan.ok else 'FAIL'}; AI may read it: {'yes' if outcome.llm_allowed else 'NO'}")
    for s in doc_states:
        echo(f"  document {s['n']} ({s['kind']}): {s['state']}")
    if rows:
        echo("  Rule results: " + ", ".join(f"{v} {k}" for k, v in summary["statuses"].items() if v))
    echo(f"  Report (officer only): {out / 'app_report.html'}")
    return out


# ---------------------------------------------------------------- report


def report(app_id, label, fields, answers, source_of, outcome, blocked_run, id_values, doc_states, facts, rows, checks, result, log,
           not_personal=()) -> str:
    esc, marked = html.escape, sb.tokens_marked
    tm = outcome.token_map
    leak_no = blocked_run.leak_scan.summary()
    src_rows = "".join(f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{esc(v)}</td><td class=muted>{esc(source_of.get(k, 'your documents'))}</td></tr>" for k, v in fields.items())
    ans_rows = "".join(f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{esc(v)}</td><td class=muted>{esc(source_of.get(k, ''))}</td></tr>" for k, v in answers.items())
    read_form = f'<pre class="doc">{marked(outcome.redacted_text)}</pre>'
    read_docs = "".join(
        f'<details><summary>Document {s["n"]} · {esc(s["kind"])} · {s["pages"]} page(s) · <strong>{esc(s["state"])}</strong></summary>'
        + (f'<pre class="doc">{marked(s["redacted"])}</pre>' if s["source"] else "<p class=muted>The AI never sees this file.</p>") + "</details>" for s in doc_states)
    token_rows = "".join(f"<tr><td>{sb.ENTITY_LABEL.get(t, t)}</td><td class=num>{n}</td></tr>" for t, n in tm.counts().items())
    ids = ", ".join(sorted({l for _, l in id_values})) or "none"
    leak_banner = (f'<div class="banner {"ok" if not leak_no else "officer"}">Leak scan on the documents as they stand: '
                   + ("PASS" if not leak_no else f"FAIL ({', '.join(f'{esc(k)} ×{v}' for k, v in leak_no.items())}). The AI would be blocked from reading this application until an officer supplies the missing identifier ({esc(ids)}) as a known value, as a form field would."))
    if not_personal:
        leak_banner += (f'</div><div class="banner warn">An officer marked these ordinary words as NOT personal for this run (the name detector mistook them for people): '
                        f'<strong>{esc(", ".join(not_personal))}</strong>. Nothing else was changed.')
    leak_banner += f"</div><div class=\"banner {'ok' if outcome.leak_scan.ok else 'officer'}\">Leak scan with the identifiers supplied ({esc(ids)}): {'PASS' if outcome.leak_scan.ok else 'FAIL'}. AI may read it: {'yes' if outcome.llm_allowed else 'NO'}.</div>"

    def quote_html(q):
        ok = '<span class="pass">found in the text</span>' if q["verified"] else '<span class="fail">NOT verified</span>'
        return (f'<div class="q"><div><small>What the AI quoted ({q["where"]}, with placeholders)</small><blockquote>{marked(q["ai"])}</blockquote></div>'
                f'<div><small>The applicant\'s words (restored for the officer)</small><blockquote>{esc(q["restored"] or "could not be restored")}</blockquote></div>{ok}</div>')

    def rule_row(r):
        how = {"code": "code", "llm": "AI finds passages", "human_only": "officer only"}[r["method"]]
        status = "Officer judgement" if r["status"] == "Evidence only" else r["status"]
        return (f'<article class="rule {STATUS_CLASS[r["status"]]}"><header><span class="code">{esc(r["code"])}</span><strong>{esc(r["text"])}</strong>'
                f'<span class="chip {STATUS_CLASS[r["status"]]}">{esc(status)}</span></header>'
                f'<p class=muted>Checked by: {how}' + (f' · confidence {r["confidence"]}' if r["confidence"] else "") + ('' if not r["clarify"] else " · ask the applicant") + "</p>"
                + (f'<p>{esc(r["why"])}</p>' if r["why"] else "") + "".join(quote_html(q) for q in r["quotes"]) + "</article>")

    sections = {"eligibility": "A. Eligibility", "documents": "B. Documents", "merit": "C. Merit (officer judges; no AI score)"}
    rules_html = "".join(f"<h2>{title}</h2>" + "".join(rule_row(r) for r in rows if r["section"] == key) for key, title in sections.items()) if rows else (
        '<div class="banner officer">The AI check did not run: the application is blocked.</div>')
    facts_html = ("<table><thead><tr><th>Fact</th><th>Value</th><th>Quote as the AI saw it</th><th></th></tr></thead><tbody>"
                  + "".join(f"<tr><td>{esc(f['key'].replace('_', ' '))}</td><td>{esc(f['value'])}</td><td>{marked(f['quote']) if f['quote'] else '<em>none</em>'}</td>"
                            f"<td>{'<span class=pass>verified</span>' if f['verified'] else ('<span class=muted>-</span>' if not f['quote'] else '<span class=fail>not verified</span>')}</td></tr>" for f in facts)
                  + "</tbody></table>") if facts else "<p class=muted>No facts were extracted.</p>"
    comp = [c for chk in checks for c in chk.comparisons]
    comp_html = ("<table><thead><tr><th>Typed answer</th><th>Compared with the document</th><th>Result</th></tr></thead><tbody>"
                 + "".join(f"<tr><td>{esc(c['field'].replace('_', ' '))}</td><td class=muted>document text</td><td>{esc(c['label'])}</td></tr>" for c in comp)
                 + "</tbody></table>") if comp else "<p class=muted>No typed-versus-document comparisons were possible.</p>"
    flags = (result.injection_flags if result else [])
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("Met", "Not met", "Needs evidence", "Unclear", "Evidence only")}
    summary = " · ".join(f'<span class="chip {STATUS_CLASS[s]}">{("Officer judgement" if s == "Evidence only" else s)} {n}</span>' for s, n in counts.items() if n)
    css = sb.CSS + """
.rule { background: var(--surface); border: 1px solid var(--line); border-left: 5px solid var(--line); border-radius: 10px; padding: 12px 16px; margin: 10px 0; display: grid; gap: 8px; }
.rule.ok { border-left-color: var(--ok); } .rule.bad { border-left-color: var(--bad); } .rule.warn { border-left-color: var(--warn); } .rule.unclear { border-left-color: #6a4fb3; } .rule.judge { border-left-color: var(--muted); }
.rule header { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; } .rule header strong { flex: 1; min-width: 220px; font-weight: 600; }
.code { font: 700 13px ui-monospace, Menlo, monospace; background: var(--token); border-radius: 4px; padding: 1px 6px; }
.chip { border-radius: 999px; padding: 2px 12px; font-size: 13px; font-weight: 700; white-space: nowrap; }
.chip.ok { background: var(--ok-bg); color: var(--ok); } .chip.bad { background: var(--bad-bg); color: var(--bad); } .chip.warn { background: var(--warn-bg); color: var(--warn); }
.chip.unclear { background: #eee8f8; color: #5b3e96; } .chip.judge { background: var(--line); color: var(--fg); }
.q { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; } .q > span { grid-column: 1 / -1; font-size: 13px; }
@media (max-width: 800px) { .q { grid-template-columns: 1fr; } }
blockquote { margin: 4px 0 0; padding: 8px 12px; background: var(--bg); border-left: 3px solid #e0b100; border-radius: 6px; font-size: 14px; }
small { color: var(--muted); } details { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; margin: 8px 0; background: var(--surface); } summary { cursor: pointer; }
h2 { margin: 18px 0 4px; }
"""
    body = f"""
<div class="banner officer">OFFICER-ONLY · contains the applicant's personal information · local file: never commit, upload or share</div>
<div><h1>What the AI reads, and what it finds</h1><p class="muted">{app_id} · AI: {esc(label)} · built from your documents · fully local</p></div>
<div class="card"><h2>How to read this page</h2><ol><li><strong>The application</strong> built from your documents, and where each answer came from.</li>
<li><strong>What the AI reads</strong>: the same text with personal details replaced by placeholders. Nothing else reaches the AI.</li>
<li><strong>What it finds</strong>: each of the 28 rules, the quote behind it as the AI saw it, and the applicant's own words restored for you.</li></ol></div>
<div class="card"><h2>1 · The application</h2><p class="muted">Form answers</p><table><thead><tr><th>Field</th><th>Answer</th><th>Source</th></tr></thead><tbody>{src_rows}</tbody></table>
<p class="muted" style="margin-top:14px">Written answers</p><table><thead><tr><th>Question</th><th>Answer</th><th>Source</th></tr></thead><tbody>{ans_rows}</tbody></table></div>
{leak_banner}
<div class="card"><h2>2 · What the AI reads</h2><p class="muted">Placeholders such as <mark class="tok">[PERSON_1]</mark> stand in for personal details; countries, providers, courses and dates other than the date of birth are kept because the rules need them.</p>
<h3>The application form</h3>{read_form}<h3 style="margin-top:14px">The documents</h3>{read_docs}
<h3 style="margin-top:14px">Replaced in total</h3><table><tbody>{token_rows}</tbody></table><p class="muted">Location class (from the address, before redaction): {esc(outcome.location_class)}</p></div>
<div class="card"><h2>3 · What the AI extracted</h2>{facts_html}<h3 style="margin-top:14px">Typed answers compared with the documents</h3>{comp_html}
{('<div class="banner officer" style="margin-top:12px">Instruction-like text found: ' + esc(', '.join(f.pattern for f in flags)) + '</div>') if flags else ''}</div>
<div class="card"><h2>4 · The result, rule by rule</h2><p>{summary}</p><p class="muted">No overall score or recommendation. The officer confirms or overrides every finding.</p>{rules_html}</div>
<p class="muted">A person must check every finding. Redaction and the stub are not guarantees. Detector {esc(outcome.report['detector_version'])}.</p>"""
    return sb.page("What the AI reads and finds", body).replace(f"<style>{sb.CSS}</style>", f"<style>{css}</style>")
