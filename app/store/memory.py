"""In-memory Store for tests and the offline evaluation run.

Emulates the database behaviours the services rely on: generated ids and
timestamps, defaults, a strictly increasing review sequence, and the
append-only audit log.
"""

from __future__ import annotations

import base64
import copy
import itertools
import functools
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from app.store.base import StoreError, now_iso

_DEFAULTS: dict[str, dict[str, Any]] = {
    "applications": {
        "status": "draft",
        "manual_assessment_requested": False,
        "ai_use_acknowledged": False,
        "ai_use_acknowledged_at": None,
        "application_text": {},
        "redacted_text": None,
        "language_style_tag": None,
        "submitted_at": None,
        "outcome_status": "in_progress",
        "eligibility_result": None,
    },
    "assessment_runs": {
        "status": "running",
        "consistency_check": False,
        "injection_flags": [],
        "finished_at": None,
        "error_message": None,
        "input_hash": None,
        "consistency_trace": {},
    },
    "consistency_flags": {"status": "open", "verification": "verified", "evidence": [], "note": None, "run_id": None,
                          "reviewed_by": None, "reviewed_at": None},
    "findings": {
        "quote_verified": False,
        "supporting_quotes": [],
        "ai_summaries": [],
        "language_flag": False,
        "needs_applicant_clarification": False,
        "is_valid": False,
        "error_flag": False,
    },
    "documents": {"pdf_layout": None, "replaces_id": None, "superseded": False, "attention_level": None, "attention_reason": None, "needs_verification": False, "verification_notes": [], "extracted_fields": {}, "is_sample": True,
                  "integrity_signals": {}},
    "application_steps": {"notice": None, "detail": {}},
    "document_reviews": {"reason": None},
    "redaction_edits": {"reason": None},
    "sign_off_history": {},
    "merit_marks": {"mark": None, "not_assessed": False, "reason": None},
    "ai_use_checks": {"fields": [], "thresholds": {}},
    "ai_use_actions": {"note": None, "request_id": None},
    "merit_runs": {"error": None, "coverage": [], "dropped": [], "overview": []},
    "merit_claims": {"document_id": None, "verified": True, "subject": "", "attribute": ""},
    "merit_summary_items": {"claim_ids": [], "documents_also": [], "differs": False},
    "merit_coverage": {"used": False, "document_id": None, "page": 1},
    "letters": {"kind": "decline", "status": "draft", "quality_checks": {}, "source_finding_ids": [], "letter_viewed_at": None, "viewed_by": None,
                "checks_passed": False, "template": False, "signed_off_by": None, "signed_off_at": None, "released_at": None, "released_by": None},
    "steps_not_assessed": {"cleared_at": None, "cleared_by": None},
    "round_decisions": {"reason_note": None, "version": 1},
    "clarification_requests": {"status": "draft", "sent_at": None, "kind": "finding", "items": [], "approved_by": None, "approved_at": None, "resubmitted_at": None},
    "audit_log": {"overridden": False, "details": {}},
    "rule_packs": {"status": "draft", "letter_config": {}},
    "rules": {"params": {}, "display_order": 0},
    "grant_programs": {"active": True},
}


def _locked(fn):
    """Writes and snapshot saves run one at a time: the API serves requests from a thread pool."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class MemoryStore:
    _lock = threading.RLock()

    def __init__(self, path: str | Path | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self._seq = itertools.count(1)
        self.files: dict[tuple[str, str], bytes] = {}  # (bucket, path) -> bytes (in-memory Storage)
        self.path = Path(path) if path else None

    @classmethod
    def load(cls, path: str | Path) -> "MemoryStore":
        store = cls(path)
        if not store.path or not store.path.exists():
            return store
        try:
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            store.tables = payload.get("tables", {})
            store.files = {
                tuple(key.split("\0", 1)): base64.b64decode(value)
                for key, value in (payload.get("files") or {}).items()
            }
            seq = max(
                (int(r["seq"]) for r in store.tables.get("officer_reviews", []) if r.get("seq") is not None),
                default=0,
            )
            store._seq = itertools.count(seq + 1)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, base64.binascii.Error) as exc:
            raise StoreError(f"could not load local demo data from {store.path}: {exc}") from exc
        return store

    @_locked
    def persist(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tables": self.tables,
            "files": {"\0".join(key): base64.b64encode(value).decode("ascii") for key, value in self.files.items()},
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    # -- Storage -----------------------------------------------------------
    @_locked
    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        if (bucket, path) in self.files:
            raise StoreError("file already exists")
        self.files[(bucket, path)] = bytes(data)
        self.persist()

    def download(self, bucket: str, path: str) -> bytes | None:
        return self.files.get((bucket, path))

    @_locked
    def remove(self, bucket: str, path: str) -> None:
        self.files.pop((bucket, path), None)
        self.persist()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _matches(row: dict[str, Any], eq: dict[str, Any] | None, in_: dict[str, list[Any]] | None) -> bool:
        for k, v in (eq or {}).items():
            if row.get(k) != v:
                return False
        for k, values in (in_ or {}).items():
            if row.get(k) not in values:
                return False
        return True

    # -- Store protocol ----------------------------------------------------
    def select(
        self,
        table: str,
        *,
        eq: dict[str, Any] | None = None,
        in_: dict[str, list[Any]] | None = None,
        order: str | None = None,
        desc: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [r for r in self.tables.get(table, []) if self._matches(r, eq, in_)]
        if order:
            rows.sort(key=lambda r: (r.get(order) is None, r.get(order)), reverse=desc)
        if limit is not None:
            rows = rows[:limit]
        return copy.deepcopy(rows)

    @_locked
    def insert(self, table: str, rows: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        batch = [rows] if isinstance(rows, dict) else rows
        out = []
        for row in batch:
            ts = now_iso()
            full = {**copy.deepcopy(_DEFAULTS.get(table, {})), **copy.deepcopy(row)}
            full.setdefault("id", str(uuid.uuid4()))
            full.setdefault("created_at", ts)
            if table != "audit_log":
                full.setdefault("updated_at", ts)
            else:
                full.setdefault("occurred_at", ts)
            if table == "officer_reviews":
                full["seq"] = next(self._seq)
                full.setdefault("reviewed_at", ts)
            if any(r["id"] == full["id"] for r in self.tables.get(table, [])):
                raise StoreError(f"duplicate key in {table}")
            self.tables.setdefault(table, []).append(full)
            out.append(copy.deepcopy(full))
        self.persist()
        return out

    @_locked
    def update(self, table: str, values: dict[str, Any], *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if table in ("audit_log",):
            raise StoreError("audit_log is append-only: UPDATE is not allowed")
        if table == "officer_reviews":
            raise StoreError("Officer reviews are append-only")
        if table == "round_decisions":
            raise StoreError("Round decisions are append-only: record a new version instead")
        if table == "letters" and any(r.get("released_at") for r in self.tables.get(table, []) if self._matches(r, eq, None)):
            raise StoreError("A released letter cannot be changed; a change needs a reconsideration and a new version")
        out = []
        for row in self.tables.get(table, []):
            if self._matches(row, eq, None):
                row.update(copy.deepcopy(values))
                row["updated_at"] = now_iso()
                out.append(copy.deepcopy(row))
        self.persist()
        return out

    @_locked
    def delete(self, table: str, *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if table == "audit_log":
            raise StoreError("audit_log is append-only: DELETE is not allowed")
        if table in ("round_decisions", "steps_not_assessed"):
            raise StoreError(f"{table} is a record: rows are never deleted")
        keep, gone = [], []
        for row in self.tables.get(table, []):
            (gone if self._matches(row, eq, None) else keep).append(row)
        self.tables[table] = keep
        self.persist()
        return gone

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn == "purge_expired_llm_data":
            before = len(self.tables.get("llm_cache", []))
            self.tables["llm_cache"] = []
            return before
        raise StoreError(f"unknown rpc {fn}")
