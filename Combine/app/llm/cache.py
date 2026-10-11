"""LLM response cache.

Key = sha256(application text the model saw + rule_pack_version +
prompt_version + model + task). Only validated outputs are cached; prompts
and keys are never stored. Entries expire after LLM_RETENTION_DAYS.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.llm.base import Prompt
from app.store.base import Store, StoreError


def cache_key(prompt: Prompt, model: str) -> str:
    payload = json.dumps(
        [prompt.cache_text, prompt.rule_pack_version, prompt.prompt_version, model, prompt.task],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Cache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any], *, provider: str, model: str, prompt: Prompt) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    def put(self, key: str, value: dict[str, Any], *, provider: str, model: str, prompt: Prompt) -> None:
        self.data[key] = value


class StoreCache:
    """Cache in the `llm_cache` table (service role only)."""

    def __init__(self, store: Store, retention_days: int | None) -> None:
        self.store = store
        self.retention_days = retention_days

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            rows = self.store.select("llm_cache", eq={"cache_key": key}, limit=1)
        except StoreError:
            return None
        if not rows:
            return None
        expires = rows[0].get("expires_at")
        if expires and datetime.fromisoformat(str(expires)) < datetime.now(timezone.utc):
            return None
        return rows[0]["response"]

    def put(self, key: str, value: dict[str, Any], *, provider: str, model: str, prompt: Prompt) -> None:
        expires = (
            (datetime.now(timezone.utc) + timedelta(days=self.retention_days)).isoformat()
            if self.retention_days
            else None
        )
        try:
            self.store.insert(
                "llm_cache",
                {
                    "cache_key": key,
                    "provider": provider,
                    "model_name": model,
                    "prompt_version": prompt.prompt_version,
                    "rule_pack_version": prompt.rule_pack_version,
                    "response": value,
                    "expires_at": expires,
                },
            )
        except StoreError:
            pass  # duplicate key from a concurrent run; cache is best-effort
