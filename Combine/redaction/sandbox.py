"""Local redaction sandbox: watch the pipeline process one document.

    python -m redaction.sandbox run <file> [--known "Full Name" "1999-07-14" country=Vietnam ...] [--inject-test]
    python -m redaction.sandbox restore <output-dir>
    python -m redaction.sandbox wipe [--yes]

Fully local: no LLM, no Supabase, no cloud service. While a command runs,
every network connection is refused. Uses the existing redaction module
(detector, tokens, leak scan, crypto, restore, span mapping) plus the
passport layer in redaction.passport. The detector is not retuned.

Where personal values may appear:
  - 1_original_view.html (officer-only view, local file), which the report
    shows in a frame instead of copying;
  - token_map.enc (AES-256-GCM, REDACTION_KEY from .env).
The terminal, logs and meta.json show tokens, counts and PASS/FAIL only;
file names are never printed or used for folder names (they often contain
names). The report's "needed information" panel lists the non-personal
terms that were kept on purpose (dates other than DOB, countries, courses).

Data folder: sandbox/ in the repo, or GRANTIE_SANDBOX_DIR. A folder synced to
a cloud service (OneDrive, iCloud, Dropbox, Google Drive) is refused: a real
document dropped there has already been uploaded. --allow-cloud-sync exists
for synthetic files only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import secrets
import socket
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = REPO / ".env"
SYNC_MARKERS = ("/library/cloudstorage/", "/library/mobile documents/", "/onedrive", "/dropbox", "/google drive", "/icloud drive")
OCR_MESSAGE = "No text layer found. Local OCR is needed"
TOKEN_FILE_FORMAT = "grantie-sandbox-tokenmap-v1"

ENTITY_LABEL = {"PERSON": "Name", "REFEREE": "Referee", "EMAIL": "Email", "PHONE": "Phone", "ADDRESS": "Address",
                "PLACE": "Place", "DOB": "Date of birth", "ID": "ID number", "URL": "URL", "HANDLE": "Handle",
                "MRZ": "Machine-readable zone"}
LOCATION_CLASSES = {
    "outside_australia": "Lives outside Australia",
    "nt_australia": "Lives in the Northern Territory",
    "australia_outside_nt": "Lives in Australia, outside the NT",
    "unknown": "Not enough information, or conflicting",
}


class SandboxError(Exception):
    """A stop with a message for the user (never contains personal values)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- safety rails


@contextmanager
def no_network() -> Iterator[None]:
    """Refuse every outbound connection and DNS lookup for the duration."""
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise OSError("network access is disabled in the redaction sandbox")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.socket.connect_ex = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved  # type: ignore[method-assign,assignment]


def quiet_libraries() -> None:
    for name in ("presidio-analyzer", "presidio_analyzer", "presidio-anonymizer", "tldextract", "filelock", "urllib3", "pdfminer", "pdfplumber", "pypdf"):
        logging.getLogger(name).setLevel(logging.ERROR)


def is_cloud_synced(path: Path) -> bool:
    s = str(path.expanduser().resolve()).lower() + "/"
    return any(m in s for m in SYNC_MARKERS)


def data_dir() -> Path:
    return Path(os.environ.get("GRANTIE_SANDBOX_DIR") or REPO / "sandbox").expanduser()


def guard_location(path: Path, what: str, allow_cloud_sync: bool) -> None:
    if is_cloud_synced(path) and not allow_cloud_sync:
        raise SandboxError(
            f"The {what} is in a folder synced to a cloud service (OneDrive, iCloud, Dropbox or Google Drive). "
            "A real document placed there is uploaded before the sandbox even runs.\n"
            "Use a plain local folder instead, for example:\n"
            "    export GRANTIE_SANDBOX_DIR=~/grantie-sandbox\n"
            "and keep your document outside cloud-synced folders. "
            "--allow-cloud-sync is for synthetic test files only.")


def load_key() -> tuple[str, str]:
    """REDACTION_KEY (and optional REDACTION_KEY_PREVIOUS) from the environment or .env. Never printed."""
    values = {k: os.environ.get(k, "") for k in ("REDACTION_KEY", "REDACTION_KEY_PREVIOUS")}
    if not values["REDACTION_KEY"] and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in values and not values[key.strip()]:
                values[key.strip()] = value.strip().strip("'\"")
    if not values["REDACTION_KEY"]:
        raise SandboxError("REDACTION_KEY is not set in .env. Add your key there and run again. "
                           "(The sandbox will not create or print a key.)")
    return values["REDACTION_KEY"], values["REDACTION_KEY_PREVIOUS"]


def cipher():
    from redaction.crypto import TokenCipher

    key, previous = load_key()
    return TokenCipher(key, previous)


# ---------------------------------------------------------------- --known parsing


def known_inputs(items: list[str]) -> tuple[dict[str, str], list]:
    """--known values -> (form fields, extra known values). Type is inferred unless given as key=value."""
    from redaction.recognizers import KnownValue, parse_any_date

    field_for = {"name": "applicant_name", "dob": "date_of_birth", "email": "email", "phone": "phone",
                 "address": "residential_address", "country": "residential_country"}
    extra_type = {"id": "ID", "passport": "ID", "place": "PLACE"}
    fields: dict[str, str] = {}
    extra: list = []
    for item in items:
        key, sep, value = item.partition("=")
        key = key.strip().lower()
        if not (sep and (key in field_for or key in extra_type)):
            value = item.strip()
            digits = sum(c.isdigit() for c in value)
            if parse_any_date(value):
                key = "dob"
            elif "@" in value:
                key = "email"
            elif digits >= 7 and digits >= len(value.replace(" ", "")) * 0.6:
                key = "phone"
            else:
                key = "name"
        value = value.strip()
        if not value:
            continue
        if key in field_for:
            if key == "name" and "applicant_name" in fields:
                extra.append(KnownValue(value, "PERSON", "known"))
            else:
                fields[field_for[key]] = value
        else:
            extra.append(KnownValue(value, extra_type[key], "known"))
    return fields, extra


# ---------------------------------------------------------------- rendering


CSS = """
:root { --bg:#f5f7f6; --surface:#fff; --fg:#1b2628; --muted:#5a6a6c; --line:#dce3e1; --accent:#0f6b73;
  --ok:#1b6b3a; --ok-bg:#e4f3e9; --bad:#b3261e; --bad-bg:#fce8e6; --warn:#8a5a00; --warn-bg:#fff1d6;
  --PERSON:#fde2c8; --REFEREE:#f8d4e4; --EMAIL:#d9e8fb; --PHONE:#dcefd9; --ADDRESS:#ece0fa; --PLACE:#e6f0c8;
  --DOB:#ffe4a3; --ID:#d3eef0; --URL:#e1e1f7; --HANDLE:#f1dcd0; --MRZ:#ffd0cc; --token:#e2f1f1; }
@media (prefers-color-scheme: dark) { :root { --bg:#121a1b; --surface:#1a2426; --fg:#e3ecea; --muted:#9db0ad;
  --line:#2c3a3c; --accent:#5cc3c9; --ok:#7fd39b; --ok-bg:#173023; --bad:#ff9a8f; --bad-bg:#3a1c19; --warn:#f0c46b;
  --warn-bg:#33290f; --PERSON:#5a3d22; --REFEREE:#55283c; --EMAIL:#24384f; --PHONE:#27402a; --ADDRESS:#3b2d52;
  --PLACE:#3c4520; --DOB:#5a4614; --ID:#1f4245; --URL:#2f2f55; --HANDLE:#4a3328; --MRZ:#5a2622; --token:#173336;
  color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system, "Segoe UI", system-ui, sans-serif; padding: 24px 16px 48px; }
main { max-width: 1280px; margin: 0 auto; display: grid; gap: 22px; }
h1 { font-size: 26px; margin: 0; } h2 { font-size: 18px; margin: 0 0 10px; } p { margin: 0; }
.muted { color: var(--muted); }
.banner { border-radius: 8px; padding: 10px 14px; font-weight: 600; }
.banner.officer { background: var(--bad-bg); color: var(--bad); }
.banner.ok { background: var(--ok-bg); color: var(--ok); }
.banner.warn { background: var(--warn-bg); color: var(--warn); }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px; min-width: 0; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
pre.doc { white-space: pre-wrap; word-break: break-word; font: 13px/1.6 ui-monospace, Menlo, monospace; margin: 0; }
iframe { width: 100%; height: 560px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.pane { max-height: 560px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
mark { border-radius: 3px; padding: 0 2px; color: inherit; }
mark.tok { background: var(--token); font-weight: 600; }
""" + "".join(f"mark.{t} {{ background: var(--{t}); }}\n" for t in ENTITY_LABEL) + """
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; } .num { text-align: right; font-variant-numeric: tabular-nums; }
.pass { color: var(--ok); font-weight: 700; } .fail { color: var(--bad); font-weight: 700; }
.legend { display: flex; flex-wrap: wrap; gap: 6px; font-size: 13px; }
.legend mark { padding: 2px 8px; }
ul { margin: 0; padding-left: 20px; }
a { color: var(--accent); }
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><style>{CSS}</style>"
            f"</head><body><main>{body}</main></body></html>")


def highlighted(text: str, spans: list[tuple[int, int, str, str]]) -> str:
    """Original text with detected spans marked by entity type (officer view)."""
    out, pos = [], 0
    for start, end, ttype, token in sorted(spans):
        if start < pos:
            continue
        out.append(html.escape(text[pos:start]))
        out.append(f'<mark class="{ttype}" title="{html.escape(token)} · {ENTITY_LABEL.get(ttype, ttype)}">'
                   f"{html.escape(text[start:end])}</mark>")
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)


def tokens_marked(text: str) -> str:
    from redaction.restore import TOKEN_RE

    out, pos = [], 0
    for m in TOKEN_RE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<mark class="tok">{html.escape(m.group())}</mark>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def legend(types: list[str]) -> str:
    return '<div class="legend">' + "".join(f'<mark class="{t}">{ENTITY_LABEL.get(t, t)}</mark>' for t in types) + "</div>"


def original_view_html(doc_id: str, text: str, spans: list[tuple[int, int, str, str]], title: str) -> str:
    types = sorted({s[2] for s in spans})
    return page(title, f"""
<div class="banner officer">OFFICER-ONLY · contains personal information · local file: never commit, upload or share</div>
<h1>{html.escape(title)}</h1><p class="muted">{doc_id} · detected spans are highlighted by type (hover for the token)</p>
{legend(types)}
<div class="card"><pre class="doc">{highlighted(text, spans)}</pre></div>""")


# ---------------------------------------------------------------- the "needed information" panel


@dataclass
class Kept:
    kind: str
    text: str          # as the AI sees it (may contain tokens)
    damaged: bool      # a token replaced part of it


def needed_information(redacted: str, location_class: str, kept_labels: list[str]) -> list[Kept]:
    import re

    from redaction.config import default_config
    from redaction.restore import TOKEN_RE

    cfg = default_config()
    items: list[Kept] = []
    seen: set[str] = set()

    def add(kind: str, text: str) -> None:
        text = text.strip()
        if text and (kind, text) not in seen:
            seen.add((kind, text))
            items.append(Kept(kind, text, bool(TOKEN_RE.search(text))))

    for line in redacted.splitlines():
        low = line.casefold()
        for label in kept_labels:
            if label in low and label not in ("type", "code"):
                add("Field kept on purpose", line)
    month = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    for m in re.finditer(rf"(?i)\b\d{{1,2}}[ /.-](?:{month}|\d{{1,2}})(?:/{month})?[ /.-]\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b", redacted):
        add("Date (not a date of birth)", m.group())
    for c in sorted(cfg.countries, key=len, reverse=True):
        if re.search(rf"(?i)(?<!\w){re.escape(c)}(?!\w)", redacted):
            add("Country", c)
    for p in cfg.allowlist_patterns():
        for m in p.finditer(redacted):
            add("Provider, course, visa or scholarship", m.group())
    for m in re.finditer(r"(?i)\b\d+(?:\.\d+)?\s*(?:/|out of|on)\s*\d+(?:\.\d+)?\b|\b(?:GPA|ATAR|IELTS|grade)\b[^\n]{0,30}", redacted):
        add("Grade or score", m.group())
    items.append(Kept("Location class", LOCATION_CLASSES.get(location_class, location_class), False))
    return items


# ---------------------------------------------------------------- run


def _doc_counts(token_map, source: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in token_map.occurrences.get(source, []) if token_map else []:
        t = token_map.entries[o.token].token_type
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items()))


def run(file: Path, known: list[str] | None = None, *, inject_test: bool = False, allow_cloud_sync: bool = False,
        ocr: bool = False, out_root: Path | None = None, echo=print) -> Path:
    from redaction.config import default_config
    from redaction.detector import DETECTOR_VERSION
    from redaction.documents import document_source
    from redaction.extract import extract_document
    from redaction.leakscan import LeakScanner
    from redaction.passport import mrz_lines_in, parse_passport
    from redaction.pipeline import run_redaction
    from redaction.storage import token_map_rows

    file = file.expanduser()
    out_root = (out_root or data_dir() / "output").expanduser()
    if not file.is_file():
        raise SandboxError("That file does not exist (or is not a file).")
    guard_location(file, "input file", allow_cloud_sync)
    guard_location(out_root, "output folder", allow_cloud_sync)
    tc = cipher()  # stop before doing any work if originals cannot be stored safely

    data = file.read_bytes()
    doc_id = "doc-" + hashlib.sha256(data).hexdigest()[:10]  # never the file name
    source = document_source(doc_id)
    quiet_libraries()

    with no_network():
        doc = extract_document(doc_id, file.name, data)
        is_pdf = doc.file_kind == "pdf"
        no_layer = doc.file_kind.startswith("image") or (is_pdf and doc.status == "no_text")
        ocr_info = None
        if no_layer or (ocr and is_pdf and doc.pages_without_text):
            if not ocr:
                raise SandboxError(OCR_MESSAGE + ". The sandbox will not guess at text in images or scans. "
                                   "Run again with --ocr to read it with local Tesseract OCR (no cloud OCR).", code=3)
            from redaction.ocr import available, ocr_document

            if not available():
                raise SandboxError("Local OCR is not installed. Install it with: brew install tesseract "
                                   "and pip install pytesseract==0.3.13", code=3)
            partial = is_pdf and doc.status == "ok"
            res = ocr_document(doc_id, file.name, data, "pdf" if is_pdf else "image",
                               base=doc if partial else None, pages=doc.pages_without_text if partial else None)
            doc = res.document
            ocr_info = {"engine": res.engine, "mean_confidence": res.mean_confidence, "needs_review": res.needs_review,
                        "mrz_replaced": res.mrz_replaced, "mrz_unresolved": res.mrz_unresolved,
                        "pages": [p.__dict__ for p in res.pages], "partial": partial}
            if doc.status != "ok":
                raise SandboxError("OCR found no readable text. A person needs to read this document.", code=3)
        if doc.status != "ok":
            raise SandboxError("Unsupported file type. Use a PDF with a text layer, a .txt file, or an image with --ocr.")

        cfg = default_config()
        passport = parse_passport(doc.text)
        fields, extra = known_inputs(known or [])
        outcome = run_redaction(doc_id, {"fields": fields}, [doc], document_kinds={doc_id: "document"},
                                cfg=cfg, extra_known=passport.known + extra)
        rdoc = outcome.documents[0]
        redacted = rdoc.redacted_text
        tm = outcome.token_map

        # Leak scan: the pipeline's scan of everything the LLM would get, plus an MRZ-line check.
        leak_checks = dict(outcome.leak_scan.summary())
        mrz_left = mrz_lines_in(redacted)
        if mrz_left:
            leak_checks["mrz_line"] = mrz_left
        from redaction.ocr import mrz_like

        garbled = sum(1 for line in redacted.splitlines() if mrz_like(line) and not mrz_lines_in(line))
        if garbled:
            leak_checks["mrz_like_line"] = garbled  # e.g. an MRZ that OCR misread: never guessed, always blocked
        leak_pass = not leak_checks

        inject = None
        if inject_test:
            occs = tm.occurrences.get(source, [])
            order = ["ID", "PERSON", "DOB", "MRZ", "PLACE", "EMAIL", "PHONE", "ADDRESS"]
            pick = sorted(occs, key=lambda o: order.index(tm.entries[o.token].token_type)
                          if tm.entries[o.token].token_type in order else 99)
            if pick:
                o = pick[0]
                injected = redacted + "\nRe-inserted for the leak test: " + o.original  # in memory only, never written
                res = LeakScanner(cfg).scan(injected, source, outcome.known_values, tm)
                checks = dict(res.summary())
                if mrz_lines_in(injected) > mrz_left:
                    checks["mrz_line"] = mrz_lines_in(injected) - mrz_left
                inject = {"token": o.token, "entity_type": tm.entries[o.token].token_type, "checks": checks,
                          "blocked": bool(checks)}
                del injected

        spans = [(o.orig_start, o.orig_end, tm.entries[o.token].token_type, o.token) for o in tm.occurrences.get(source, [])]
        counts = _doc_counts(tm, source)
        pass1 = _doc_counts(outcome.pass1_token_map, source)
        token_rows = []
        seen_tokens: dict[str, int] = {}
        for o in sorted(tm.occurrences.get(source, []), key=lambda o: o.red_start):
            seen_tokens[o.token] = seen_tokens.get(o.token, 0) + 1
        for token, n in seen_tokens.items():
            token_rows.append((tm.entries[token].token_type, token, n))
        kept = needed_information(redacted, outcome.location_class, passport.kept_labels)

        # ---- write outputs
        out = out_root / doc_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "1_original_view.html").write_text(original_view_html(doc_id, doc.text, spans, "Original view (officer only)"), encoding="utf-8")
        (out / "2_redacted.txt").write_text(redacted, encoding="utf-8")
        rows = token_map_rows(doc_id, None, tm, tc)
        blob = tc.encrypt(doc_id, "token_map", json.dumps(rows, ensure_ascii=False))
        (out / "token_map.enc").write_text(json.dumps({"format": TOKEN_FILE_FORMAT, "doc_id": doc_id, "blob": blob}), encoding="utf-8")
        meta = {
            "doc_id": doc_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "file_kind": doc.file_kind, "text_source": ("text layer + local OCR" if ocr_info and ocr_info["partial"]
                                                        else "local OCR" if ocr_info else "text layer"),
            "ocr": ocr_info, "pages": len(doc.pages), "pages_without_text": doc.pages_without_text,
            "pdf_metadata_keys": doc.metadata_keys, "status": outcome.status, "ai_status": outcome.ai_status,
            "location_class": outcome.location_class, "counts": counts, "pass1_counts": pass1,
            "leak_pass": leak_pass, "leak_checks": leak_checks, "inject_test": inject,
            "mrz": {"lines": len(passport.mrz_lines), "td3_parsed": passport.td3_parsed, "check_digits_ok": passport.checks_ok,
                    "fields_used": passport.derived},
            "original_sha256": hashlib.sha256(doc.text.encode("utf-8")).hexdigest(),
            "redacted_sha256": hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
            "detector_version": DETECTOR_VERSION, "config_hash": cfg.config_hash,
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (out / "3_report.html").write_text(report_html(meta, redacted, token_rows, kept), encoding="utf-8")
        write_index(out_root)

    # ---- terminal summary: tokens, counts and PASS/FAIL only
    echo(f"Processed {doc_id} ({doc.file_kind}, {len(doc.pages)} page(s)) - fully local, network blocked")
    if ocr_info:
        low = sum(p["low_confidence_words"] for p in ocr_info["pages"])
        echo(f"  Text source: {meta['text_source']} ({ocr_info['engine']}); mean confidence {ocr_info['mean_confidence']}%, "
             f"{low} low-confidence word(s)" + ("; NEEDS A PERSON TO CHECK THE READING" if ocr_info["needs_review"] else ""))
        if ocr_info["mrz_replaced"] or ocr_info["mrz_unresolved"]:
            echo(f"  MRZ OCR: {ocr_info['mrz_replaced']} line(s) corrected by the MRZ-only pass, "
                 f"{ocr_info['mrz_unresolved']} left unresolved (blocked by the leak scan)")
    if doc.pages_without_text:
        echo(f"  ! {len(doc.pages_without_text)} page(s) have no text layer and were NOT redacted. {OCR_MESSAGE} for them.")
    if passport.mrz_lines:
        echo(f"  MRZ: {len(passport.mrz_lines)} line(s) replaced; passport format parsed: {'yes' if passport.td3_parsed else 'no'}; "
             f"check digits: {'all valid' if passport.checks_ok and all(passport.checks_ok.values()) else 'not all valid' if passport.checks_ok else 'n/a'}")
    echo("  Tokens:")
    for ttype, token, n in token_rows:
        echo(f"    {token:<14} {ENTITY_LABEL.get(ttype, ttype):<24} x{n}")
    added = sum(counts.values()) - sum(pass1.values())
    echo(f"  Two-pass consistency: pass 1 replaced {sum(pass1.values())}, final {sum(counts.values())} "
         f"({'+' if added >= 0 else ''}{added} from pass 2)")
    echo(f"  Location class: {outcome.location_class}")
    echo(f"  Leak scan: {'PASS' if leak_pass else 'FAIL'}" + ("" if leak_pass else f" ({', '.join(f'{k} x{v}' for k, v in leak_checks.items())}) - the LLM call would be blocked"))
    if inject:
        echo(f"  Inject test: re-inserted the original of {inject['token']} -> leak scan "
             f"{'FAIL, the LLM call would be blocked' if inject['blocked'] else 'PASS (NOT caught)'}"
             + (f" ({', '.join(inject['checks'])})" if inject["checks"] else ""))
    elif inject_test:
        echo("  Inject test: nothing was redacted, so there is nothing to re-insert.")
    echo(f"  Kept on purpose: {sum(1 for k in kept if not k.damaged)} item(s); {sum(1 for k in kept if k.damaged)} partly replaced (see the report)")
    echo(f"  Output: {out}")
    echo(f"  Open:   {out / '3_report.html'}")
    return out


def report_html(meta: dict[str, Any], redacted: str, token_rows: list[tuple[str, str, int]], kept: list[Kept]) -> str:
    doc_id = meta["doc_id"]
    leak = ('<span class="pass">PASS</span> · nothing personal found in the text the LLM would receive' if meta["leak_pass"]
            else '<span class="fail">FAIL</span> · the LLM call would be blocked: ' + ", ".join(f"{html.escape(k)} ×{v}" for k, v in meta["leak_checks"].items()))
    inj = meta.get("inject_test")
    inject_html = "<p class=\"muted\">Run with <code>--inject-test</code> to re-insert one original value and watch the leak scan catch it.</p>"
    if inj:
        inject_html = (f"<p>Re-inserted the original of <mark class=\"tok\">{html.escape(inj['token'])}</mark> "
                       f"({ENTITY_LABEL.get(inj['entity_type'], inj['entity_type'])}) into the redacted text, in memory only. Result: "
                       + ('<span class="fail">FAIL</span> · blocked by ' + ", ".join(html.escape(c) for c in inj["checks"]) if inj["blocked"]
                          else '<span class="fail">NOT CAUGHT</span>') + ".</p>")
    types = sorted(set(meta["counts"]) | set(meta["pass1_counts"]))
    two_pass = "".join(f"<tr><td>{ENTITY_LABEL.get(t, t)}</td><td class=num>{meta['pass1_counts'].get(t, 0)}</td>"
                       f"<td class=num>{meta['counts'].get(t, 0)}</td></tr>" for t in types)
    added = sum(meta["counts"].values()) - sum(meta["pass1_counts"].values())
    tokens = "".join(f"<tr><td>{ENTITY_LABEL.get(t, t)}</td><td><mark class=tok>{html.escape(tok)}</mark></td><td class=num>{n}</td></tr>"
                     for t, tok, n in token_rows) or "<tr><td colspan=3>Nothing was replaced.</td></tr>"
    loc = "".join(f"<tr><td>{'<strong>' if k == meta['location_class'] else ''}{k}{'</strong> ← this document' if k == meta['location_class'] else ''}</td><td>{v}</td></tr>"
                  for k, v in LOCATION_CLASSES.items())
    kept_rows = "".join(f"<tr><td>{html.escape(k.kind)}</td><td>{tokens_marked(k.text)}</td><td>{'⚠ partly replaced' if k.damaged else 'kept'}</td></tr>" for k in kept)
    m = meta["mrz"]
    mrz = ("No machine-readable zone found." if not m["lines"] else
           f"{m['lines']} MRZ line(s) replaced as whole lines. Passport format parsed: {'yes' if m['td3_parsed'] else 'no'}. "
           f"Check digits: {', '.join(f'{k} ' + ('valid' if v else 'INVALID') for k, v in m['check_digits_ok'].items()) or 'n/a'}. "
           f"Used as known values: {', '.join(m['fields_used'])}.")
    o = meta.get("ocr")
    ocr_banner = "" if not o else (
        f'<div class="banner {"officer" if o["needs_review"] else "warn"}">Text read by local OCR ({html.escape(o["engine"])}): '
        f'mean confidence {o["mean_confidence"]}%, {sum(p["low_confidence_words"] for p in o["pages"])} low-confidence word(s), '
        f'MRZ lines corrected {o["mrz_replaced"]}, unresolved {o["mrz_unresolved"]}. '
        + ("A person must check the reading before this is used. " if o["needs_review"] else "")
        + "OCR mistakes can stop a name or number from being recognised: read the redacted text against the document.</div>")
    missing = (f'<div class="banner warn">{len(meta["pages_without_text"])} page(s) have no text layer and were not redacted. {OCR_MESSAGE} for them.</div>'
               if meta["pages_without_text"] else "")
    return page(f"Redaction report {doc_id}", f"""
<div class="banner officer">OFFICER-ONLY · the left pane shows the original document · local file: never commit, upload or share</div>
<div><h1>Redaction report</h1><p class="muted">{doc_id} · {meta['file_kind']}, {meta['pages']} page(s), {html.escape(meta['text_source'])} · {meta['created_at']} · fully local, no network, no LLM</p></div>
{ocr_banner}{missing}
<div class="banner {'ok' if meta['leak_pass'] else 'officer'}">Leak scan: {leak}</div>
<div class="grid2">
  <div class="card"><h2>Original with detections <span class="muted">(officer only)</span></h2>
    <iframe src="1_original_view.html" title="Original view (officer only)"></iframe></div>
  <div class="card"><h2>Redacted text <span class="muted">(exactly what an LLM would receive)</span></h2>
    <div class="pane"><pre class="doc">{tokens_marked(redacted)}</pre></div></div>
</div>
<div class="grid2">
  <div class="card"><h2>Tokens</h2><table><thead><tr><th>Entity type</th><th>Token</th><th class=num>Count</th></tr></thead><tbody>{tokens}</tbody></table>
    <p class="muted" style="margin-top:8px">No original values on this side. The mapping is in token_map.enc (AES-256-GCM).</p></div>
  <div class="card"><h2>Needed information kept</h2><p class="muted" style="margin-bottom:8px">Deliberately not redacted: the rules need these. “Partly replaced” means the detector over-redacted part of it.</p>
    <table><thead><tr><th>What</th><th>As the AI sees it</th><th></th></tr></thead><tbody>{kept_rows}</tbody></table></div>
</div>
<div class="grid2">
  <div class="card"><h2>Two-pass consistency</h2><p class="muted" style="margin-bottom:8px">Pass 2 re-redacts knowing every value pass 1 found, so a value hidden in one place is hidden everywhere.</p>
    <table><thead><tr><th>Type</th><th class=num>Pass 1</th><th class=num>Final</th></tr></thead><tbody>{two_pass}</tbody></table>
    <p style="margin-top:8px">{'Pass 2 added ' + str(added) + ' replacement(s).' if added > 0 else 'Pass 2 added nothing: pass 1 was already consistent.' if added == 0 else 'Pass 2 has fewer replacements (overlaps merged).'}</p></div>
  <div class="card"><h2>Location class</h2><p class="muted" style="margin-bottom:8px">Worked out before redaction from the address and country (pass <code>--known country=…</code>); the AI sees the class, never the address.</p>
    <table><tbody>{loc}</tbody></table></div>
</div>
<div class="grid2">
  <div class="card"><h2>Machine-readable zone</h2><p>{mrz}</p></div>
  <div class="card"><h2>Leak test</h2>{inject_html}</div>
</div>
<p class="muted">Detector {html.escape(meta['detector_version'])} · config {meta['config_hash'][:12]} · redaction is not a guarantee: read the redacted text before relying on it.</p>""")


# ---------------------------------------------------------------- index


def write_index(out_root: Path) -> None:
    rows = []
    for meta_file in sorted(out_root.glob("doc-*/meta.json")):
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = meta_file.parent.name
        restored = (meta_file.parent / "4_restored_view.html").exists()
        rows.append(f"<tr><td>{html.escape(d)}</td><td>{html.escape(m.get('created_at', ''))}</td><td>{html.escape(m.get('file_kind', ''))}</td>"
                    f"<td class=num>{sum(m.get('counts', {}).values())}</td><td>{'<span class=pass>PASS</span>' if m.get('leak_pass') else '<span class=fail>FAIL</span>'}</td>"
                    f"<td><a href=\"{d}/3_report.html\">report</a> · <a href=\"{d}/1_original_view.html\">original</a> · "
                    f"<a href=\"{d}/2_redacted.txt\">redacted</a>{f' · <a href={chr(34)}{d}/4_restored_view.html{chr(34)}>restored</a>' if restored else ''}</td></tr>")
    body = ("<div class=\"banner officer\">OFFICER-ONLY · local sandbox outputs · never commit, upload or share</div>"
            "<h1>Redaction sandbox</h1><p class=\"muted\">Documents processed on this machine. Folder names are content hashes, not file names.</p>"
            "<div class=\"card\"><table><thead><tr><th>Document</th><th>Processed (UTC)</th><th>Kind</th><th class=num>Replaced</th>"
            "<th>Leak scan</th><th>Open</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan=6>Nothing processed yet.</td></tr>")
            + "</tbody></table></div><p class=\"muted\">Delete everything with <code>python -m redaction.sandbox wipe</code>.</p>")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "index.html").write_text(page("Redaction sandbox", body), encoding="utf-8")


# ---------------------------------------------------------------- restore


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, _table: str, eq: dict[str, Any] | None = None, **_: Any) -> list[dict[str, Any]]:
        return [r for r in self.rows if all(r.get(k) == v for k, v in (eq or {}).items())]


def restore_dir(out_dir: Path, *, echo=print) -> Path:
    from redaction.config import default_config
    from redaction.crypto import DecryptionFailed
    from redaction.documents import document_source
    from redaction.restore import restore
    from redaction.storage import load_token_map

    out_dir = out_dir.expanduser()
    try:
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        redacted = (out_dir / "2_redacted.txt").read_text(encoding="utf-8")
        envelope = json.loads((out_dir / "token_map.enc").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SandboxError("That is not a sandbox output folder (meta.json, 2_redacted.txt and token_map.enc are needed).") from exc
    if envelope.get("format") != TOKEN_FILE_FORMAT:
        raise SandboxError("token_map.enc is not in a format this sandbox understands.")
    doc_id = meta["doc_id"]
    tc = cipher()
    with no_network():
        try:
            rows = json.loads(tc.decrypt(doc_id, "token_map", envelope["blob"]))
            tm = load_token_map(_Rows(rows), doc_id, tc, default_config())
        except DecryptionFailed as exc:
            raise SandboxError("The token map could not be decrypted with this REDACTION_KEY (wrong key, or the file was changed).") from exc
        source = document_source(doc_id)
        original = restore(redacted, tm, source)
        exact = hashlib.sha256(original.encode("utf-8")).hexdigest() == meta["original_sha256"]
        spans = [(o.orig_start, o.orig_end, tm.entries[o.token].token_type, o.token) for o in tm.occurrences.get(source, [])]
        (out_dir / "4_restored_view.html").write_text(
            original_view_html(doc_id, original, spans, "Restored view (officer only, from the encrypted token map)"), encoding="utf-8")
        write_index(out_dir.parent)
    echo(f"Restored {doc_id} from 2_redacted.txt + token_map.enc ({len(tm.entries)} token(s) decrypted)")
    echo(f"  Matches the original exactly: {'yes' if exact else 'NO - the redacted text or map has changed'}")
    echo(f"  Open: {out_dir / '4_restored_view.html'}")
    if not exact:
        raise SandboxError("Restore did not reproduce the original exactly.", code=4)
    return out_dir / "4_restored_view.html"


# ---------------------------------------------------------------- wipe


def wipe(*, yes: bool = False, root: Path | None = None, echo=print, ask=input) -> int:
    root = (root or data_dir()).expanduser()
    targets = [p for sub in ("input", "output") for p in (root / sub).rglob("*") if p.is_file() and p.name != ".gitkeep"]
    if not targets:
        echo("Nothing to wipe in input/ and output/.")
        return 0
    echo(f"This permanently deletes {len(targets)} file(s) in {root / 'input'} and {root / 'output'}.")
    if not yes and ask("Type WIPE to confirm: ").strip() != "WIPE":
        echo("Cancelled. Nothing was deleted.")
        return 0
    for p in targets:
        try:
            size = p.stat().st_size
            with open(p, "r+b") as fh:  # overwrite before unlinking
                fh.write(secrets.token_bytes(max(size, 1)))
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass
        p.unlink(missing_ok=True)
    for sub in ("input", "output"):
        for d in sorted((root / sub).rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass
        (root / sub).mkdir(parents=True, exist_ok=True)
    echo(f"Deleted {len(targets)} file(s). Note: on SSDs, APFS snapshots, Time Machine or cloud-synced folders, "
         "overwriting cannot guarantee old copies are gone; check those separately.")
    return len(targets)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m redaction.sandbox", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="redact one document locally and write the views")
    r.add_argument("file", type=Path)
    r.add_argument("--known", nargs="*", default=[], metavar="VALUE",
                   help='values from the form, e.g. "Full Name" 1999-07-14 email@x.y; or typed: name=… dob=… id=… place=… country=…')
    r.add_argument("--inject-test", action="store_true", help="re-insert one original value and show the leak scan catching it")
    r.add_argument("--ocr", action="store_true", help="read images and scanned pages with local Tesseract OCR")
    r.add_argument("--allow-cloud-sync", action="store_true", help="allow a cloud-synced folder (synthetic files only)")
    a = sub.add_parser("assess", help="build one application from a folder of documents; show what the AI reads and finds")
    a.add_argument("folder", type=Path)
    a.add_argument("--known", nargs="*", default=[], metavar="VALUE", help='the applicant: "Full Name" 2003/03/14')
    a.add_argument("--answers", type=Path, help="folder with <field>.txt written answers missing from the documents")
    a.add_argument("--llm", choices=["stub", "gemini"], default="stub",
                   help="stub (default): local keyword matcher, not an LLM. gemini: sends the REDACTED text to Gemini")
    a.add_argument("--not-personal", nargs="*", default=[], metavar="WORD",
                   help="ordinary words the name detector mistook for people, e.g. Code Email (this run only)")
    a.add_argument("--allow-cloud-sync", action="store_true")
    rs = sub.add_parser("restore", help="decrypt the token map and rebuild the original view")
    rs.add_argument("output_dir", type=Path)
    w = sub.add_parser("wipe", help="securely delete everything in sandbox input/ and output/")
    w.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            run(args.file, args.known, inject_test=args.inject_test, allow_cloud_sync=args.allow_cloud_sync, ocr=args.ocr)
        elif args.cmd == "assess":
            from redaction.sandbox_assess import assess_folder

            assess_folder(args.folder, args.known, answers_dir=args.answers, llm=args.llm, not_personal=args.not_personal,
                         allow_cloud_sync=args.allow_cloud_sync)
        elif args.cmd == "restore":
            restore_dir(args.output_dir)
        else:
            wipe(yes=args.yes)
    except SandboxError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
