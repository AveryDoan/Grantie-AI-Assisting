"""In-memory Store for tests and the offline evaluation run.

Emulates the database behaviours the services rely on: generated ids and
timestamps, defaults, a strictly increasing review sequence, and the
append-only audit log.
"""

from __future__ import annotations

import copy
import itertools
import uuid
from typing import Any

from app.store.base import StoreError, now_iso

_DEFAULTS: dict[str, dict[str, Any]] = {
    "applications": {
        "status": "draft",
        "manual_assessment_requested": False,
        "application_text": {},
        "redacted_text": None,
        "language_style_tag": None,
        "submitted_at": None,
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
    "documents": {"attention_level": None, "attention_reason": None, "needs_verification": False, "verification_notes": [], "extracted_fields": {}, "is_sample": True,
                  "integrity_signals": {}},
    "letters": {"status": "draft", "quality_checks": {}, "source_finding_ids": []},
    "clarification_requests": {"status": "draft"},
    "audit_log": {"overridden": False, "details": {}},
    "rule_packs": {"status": "draft", "letter_config": {}},
    "rules": {"params": {}, "display_order": 0},
    "grant_programs": {"active": True},
}


class MemoryStore:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self._seq = itertools.count(1)
        self.files: dict[tuple[str, str], bytes] = {}  # (bucket, path) -> bytes (in-memory Storage)

    # -- Storage -----------------------------------------------------------
    def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        if (bucket, path) in self.files:
            raise StoreError("file already exists")
        self.files[(bucket, path)] = bytes(data)

    def download(self, bucket: str, path: str) -> bytes | None:
        return self.files.get((bucket, path))

    def remove(self, bucket: str, path: str) -> None:
        self.files.pop((bucket, path), None)

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
        return out

    def update(self, table: str, values: dict[str, Any], *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if table in ("audit_log",):
            raise StoreError("audit_log is append-only: UPDATE is not allowed")
        if table == "officer_reviews":
            raise StoreError("Officer reviews are append-only")
        out = []
        for row in self.tables.get(table, []):
            if self._matches(row, eq, None):
                row.update(copy.deepcopy(values))
                row["updated_at"] = now_iso()
                out.append(copy.deepcopy(row))
        return out

    def delete(self, table: str, *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if table == "audit_log":
            raise StoreError("audit_log is append-only: DELETE is not allowed")
        keep, gone = [], []
        for row in self.tables.get(table, []):
            (gone if self._matches(row, eq, None) else keep).append(row)
        self.tables[table] = keep
        return gone

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn == "purge_expired_llm_data":
            before = len(self.tables.get("llm_cache", []))
            self.tables["llm_cache"] = []
            return before
        raise StoreError(f"unknown rpc {fn}")
