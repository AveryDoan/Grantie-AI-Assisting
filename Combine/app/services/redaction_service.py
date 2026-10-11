"""Redaction as a service: run + persist, report, officer-only original view.

- run_and_store(): idempotent. The same application text, documents, config
  and detector version give the same run (no new rows). Persists the run
  record (counts only), the encrypted token map, the redacted texts,
  location_class and ai_status, and writes an audit entry without values.
- report(): status and counts, never values (file names are left out too:
  they often contain names).
- original_view(): officers/admins only, audited; restores the stored
  redacted texts exactly via the encrypted token map.
- QuoteRestorer: turns AI quotes (which contain tokens) back into the
  applicant's own words for letters and the review screen.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import Settings
from app.logging_utils import get_logger
from app.services.access import Actor, application_for_staff, require_role
from app.services.audit import write_audit
from app.services.errors import Conflict
from app.store.base import Store, now_iso, one

log = get_logger(__name__)
BUCKET = "application-documents"
APPLICATION_SOURCE = "application_text"


class RedactionNotConfigured(Conflict):
    code = "redaction_not_configured"


class RedactionBlockedError(Conflict):
    code = "redaction_blocked"


def _cipher(settings: Settings):
    from redaction.crypto import RedactionKeyMissing, cipher_from_settings

    try:
        return cipher_from_settings(settings)
    except RedactionKeyMissing as exc:
        raise RedactionNotConfigured("REDACTION_KEY is not set: redaction results cannot be stored safely") from exc


def document_bytes(store: Store, row: dict[str, Any]) -> tuple[bytes, str]:
    """(bytes, file name) for extraction: the stored file if available, else its stored text."""
    download = getattr(store, "download", None)
    data = download(BUCKET, row["storage_path"]) if download and row.get("storage_path") else None
    name = row.get("file_name") or "document"
    if data is None:
        data = (row.get("extracted_text") or "").encode("utf-8")
        if not name.lower().endswith(".txt"):
            name += ".txt"
    return data, name


def extracted_documents(store: Store, rows: list[dict[str, Any]]):
    from redaction.extract import extract_document

    out = []
    for row in rows:
        data, name = document_bytes(store, row)
        out.append((row, extract_document(row["id"], name, data), hashlib.sha256(data).hexdigest()))
    return out


def document_kinds(extracted) -> dict[str, str]:
    from app.pipeline.documents import classify

    return {doc.document_id: ("referee_letter" if classify(doc.text) == "referee_letter" else "document")
            for _, doc, _ in extracted}


def input_hash(application_text: dict[str, Any], extracted) -> str:
    from redaction.config import default_config
    from redaction.detector import DETECTOR_VERSION

    payload = json.dumps([application_text, sorted((d.document_id, h) for _, d, h in extracted),
                          default_config().config_hash, DETECTOR_VERSION], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_edits(store: Store, application_id: str, cipher: Any) -> tuple[list[Any], list[str]]:
    """The officer's redaction edits: values to ADD as missed (known values) and values to leave UNMASKED (allowlist).

    Values are stored encrypted and only decrypted here, in memory, to run the redaction again."""
    from redaction.recognizers import KnownValue

    extra: list[KnownValue] = []
    allow: list[str] = []
    for e in store.select("redaction_edits", eq={"application_id": application_id}):
        value = cipher.decrypt(application_id, f"edit:{e['kind']}", e["encrypted_value"])
        if e["kind"] == "add":
            extra.append(KnownValue(value, e["token_type"], "officer"))
        else:
            allow.append(value)
    return extra, allow


def redact_in_memory(app: dict[str, Any], doc_rows: list[dict[str, Any]], store: Store, edits: tuple[list[Any], list[str]] | None = None):
    """Run the redaction pipeline without persisting (pure)."""
    from redaction.config import default_config
    from redaction.pipeline import run_redaction

    extracted = extracted_documents(store, doc_rows)
    extra, allow = edits or ([], [])
    cfg = default_config()
    if allow:   # an officer said these are not personal: the detector leaves them alone (known personal values still win)
        cfg = cfg.model_copy(update={"allowlist": [*cfg.allowlist, *allow]})
    outcome = run_redaction(app["id"], app.get("application_text") or {}, [d for _, d, _ in extracted],
                            document_kinds=document_kinds(extracted), cfg=cfg, extra_known=extra)
    return outcome, extracted


def run_and_store(store: Store, actor: Actor, application_id: str, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    from redaction.config import default_config
    from redaction.detector import DETECTOR_VERSION
    from redaction.storage import save_token_map

    app = application_for_staff(store, actor, application_id)
    cipher = _cipher(settings)  # fail before doing any work if originals cannot be stored safely
    doc_rows = store.select("documents", eq={"application_id": application_id})
    edits = load_edits(store, application_id, cipher)
    outcome, extracted = redact_in_memory(app, [d for d in doc_rows if not d.get("superseded")], store, edits)
    digest = input_hash(app.get("application_text") or {}, extracted) + (":e" + str(len(edits[0]) + len(edits[1])) if edits[0] or edits[1] else "")

    if not force:
        previous = one(store.select("redaction_runs", eq={"application_id": application_id, "input_hash": digest},
                                    order="started_at", desc=True, limit=1))
        if previous and previous["status"] != "running":
            write_audit(store, actor, "redaction.run", application_id=application_id,
                        details={"run_id": previous["id"], "status": previous["status"], "reused": True})
            return {"run": previous, "reused": True, "outcome": outcome}

    cfg = default_config()
    run = store.insert("redaction_runs", {
        "application_id": application_id, "status": "running", "detector_version": DETECTOR_VERSION,
        "config_hash": cfg.config_hash, "input_hash": digest,
    })[0]
    save_token_map(store, application_id, run["id"], outcome.token_map, cipher)
    store.update("applications", {"redacted_text": outcome.redacted_text, "location_class": outcome.location_class,
                                  "ai_status": outcome.ai_status}, eq={"id": application_id})
    by_id = {d.document_id: d for d in outcome.documents}
    for row, ext, _ in extracted:
        rd = by_id[row["id"]]
        values: dict[str, Any] = {"redacted_text": rd.redacted_text or None, "extraction_status": rd.extraction_status,
                                  "needs_manual_review": rd.needs_manual_review}
        if ext.text and ext.file_kind == "pdf":
            values["extracted_text"] = ext.text  # officer view of a real PDF (staff-only table)
        store.update("documents", values, eq={"id": row["id"]})
    run = store.update("redaction_runs", {
        "status": outcome.status, "finished_at": now_iso(), "counts": outcome.report["counts"],
        "leak_scan": outcome.report["leak_scan"],
    }, eq={"id": run["id"]})[0]
    write_audit(store, actor, "redaction.run", application_id=application_id, details={
        "run_id": run["id"], "status": outcome.status, "ai_status": outcome.ai_status,
        "counts": outcome.report["counts"], "leak_scan": outcome.report["leak_scan"],
        "documents": outcome.report["documents"], "low_confidence": outcome.report["low_confidence_detections"],
    })
    return {"run": run, "reused": False, "outcome": outcome}


def report(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    app = application_for_staff(store, actor, application_id)
    write_audit(store, actor, "redaction.report_viewed", application_id=application_id)
    run = one(store.select("redaction_runs", eq={"application_id": application_id}, order="started_at", desc=True, limit=1))
    docs = [d for d in store.select("documents", eq={"application_id": application_id}) if not d.get("superseded")]
    return {
        "application_id": application_id,
        "ai_status": app.get("ai_status", "not_redacted"),
        "location_class": app.get("location_class"),
        "run": None if not run else {k: run.get(k) for k in (
            "id", "status", "started_at", "finished_at", "counts", "leak_scan", "detector_version", "config_hash")},
        # Which tokens exist and where they occur - never the original values.
        "tokens": _token_summary(store, application_id),
        "documents": [
            {"document_id": d["id"], "declared_type": d.get("declared_type"), "extraction_status": d.get("extraction_status"),
             "needs_manual_review": bool(d.get("needs_manual_review")),
             "included_in_ai_input": bool(d.get("redacted_text"))}
            for d in sorted(docs, key=lambda d: d["id"])
        ],
    }


def _token_summary(store: Store, application_id: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(store.select("redaction_token_maps", eq={"application_id": application_id}), key=lambda r: r["ordinal"]):
        t = out.setdefault(row["token"], {"token": row["token"], "entity_type": row["entity_type"], "occurrences": 0, "sources": []})
        t["occurrences"] += max(1, len(row.get("spans") or []))
        if row["text_key"] not in t["sources"]:
            t["sources"].append(row["text_key"])
    return list(out.values())


def original_view(store: Store, actor: Actor, application_id: str, settings: Settings) -> dict[str, Any]:
    from redaction.restore import restore
    from redaction.storage import load_token_map
    from redaction.structured import original_application_text

    require_role(actor, "officer", "admin")
    app = application_for_staff(store, actor, application_id)
    if not app.get("redacted_text") or app.get("ai_status") in (None, "not_redacted"):
        raise Conflict("This application has not been redacted yet")
    tokens = load_token_map(store, application_id, _cipher(settings))
    restored = restore(app["redacted_text"], tokens, APPLICATION_SOURCE)
    docs = []
    for d in sorted(store.select("documents", eq={"application_id": application_id}), key=lambda d: d["id"]):
        if d.get("redacted_text") and not d.get("superseded"):
            docs.append({"document_id": d["id"], "text": restore(d["redacted_text"], tokens, f"document:{d['id']}")})
    write_audit(store, actor, "redaction.original_view", application_id=application_id,
                details={"documents": len(docs)})  # who looked and when - never what they saw
    return {
        "application_id": application_id,
        "application_text": restored,
        "matches_stored_original": restored == original_application_text(app.get("application_text") or {}),
        "documents": docs,
    }


class QuoteRestorer:
    """Turns AI quotes (with tokens) into the applicant's own words. Officer views only."""

    def __init__(self, store: Store, app: dict[str, Any], settings: Settings) -> None:
        self.store, self.app, self.settings = store, app, settings
        self._tokens = None
        self._texts: dict[str, tuple[str, str]] | None = None  # source -> (redacted, original)

    def _load(self) -> None:
        if self._tokens is not None:
            return
        from redaction.storage import load_token_map
        from redaction.structured import original_application_text

        try:
            self._tokens = load_token_map(self.store, self.app["id"], _cipher(self.settings))
        except Exception:
            self._tokens = False  # no map available: quotes are shown as stored (tokens), never guessed
            return
        self._texts = {APPLICATION_SOURCE: (self.app.get("redacted_text") or "",
                                            original_application_text(self.app.get("application_text") or {}))}
        for row, ext, _ in extracted_documents(self.store, [d for d in self.store.select("documents", eq={"application_id": self.app["id"]}) if not d.get("superseded")]):
            if row.get("redacted_text"):
                self._texts[f"document:{row['id']}"] = (row["redacted_text"], ext.text)

    def locate(self, quote: str | None, source: str | None = None) -> dict[str, Any] | None:
        """Where an AI quote sits in the ORIGINAL text, from the exact span mapping: {source, start, end, text, method}.

        None when there is no token map or the passage cannot be found. A position is never guessed."""
        if not quote:
            return None
        self._load()
        if not self._tokens:
            return None
        from redaction.spans import quote_to_original

        sources = [source] if source and source in (self._texts or {}) else list(self._texts or {})
        for s in sources:
            red, orig = self._texts[s]
            m = quote_to_original(quote, red, orig, self._tokens, s)
            if m is not None:
                return {"source": s, "start": m.orig_start, "end": m.orig_end, "text": m.original, "method": m.method}
        return None

    def original(self, quote: str | None, source: str | None = None) -> str | None:
        if not quote:
            return quote
        hit = self.locate(quote, source)
        if hit:
            return hit["text"]
        self._load()
        if not self._tokens:
            return quote
        from redaction.restore import restore

        return restore(quote, self._tokens)  # canonical display; ambiguous placeholders stay as tokens

    def text(self, value: str | None) -> str | None:
        """Restore tokens in free text such as an AI summary (ambiguous placeholders stay as tokens)."""
        if not value:
            return value
        self._load()
        if not self._tokens:
            return value
        from redaction.restore import restore

        return restore(value, self._tokens)

    def source_texts(self) -> dict[str, dict[str, str]]:
        """The original texts the offsets from locate() point into. Officer view only."""
        self._load()
        if not self._tokens:
            return {}
        out: dict[str, dict[str, str]] = {APPLICATION_SOURCE: {"label": "Application form", "text": self._texts[APPLICATION_SOURCE][1]}}
        for row in self.store.select("documents", eq={"application_id": self.app["id"]}):
            key = f"document:{row['id']}"
            if key in (self._texts or {}):
                out[key] = {"label": f"{(row.get('declared_type') or 'document').replace('_', ' ').capitalize()} · {row.get('file_name')}",
                            "text": self._texts[key][1]}
        return out
