"""Store backed by Supabase (PostgREST) using the server-side secret key.

The secret key bypasses RLS, so every service function checks the caller's
role and organisation before touching data (see app.services.access).
Integrity triggers in the database still run and are surfaced as StoreError.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.store.base import StoreError


class SupabaseStore:
    def __init__(self, settings: Settings) -> None:
        from supabase import create_client  # imported lazily: tests don't need it

        key = settings.supabase_secret_key.get_secret_value()
        if not settings.supabase_url or not key:
            raise StoreError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set")
        self.client = create_client(settings.supabase_url, key)

    def _run(self, query: Any) -> list[dict[str, Any]]:
        try:
            return query.execute().data or []
        except Exception as exc:  # postgrest.APIError, httpx errors
            message = getattr(exc, "message", None) or str(exc)
            raise StoreError(message) from exc

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
        q = self.client.table(table).select("*")
        for k, v in (eq or {}).items():
            q = q.is_(k, "null") if v is None else q.eq(k, v)
        for k, values in (in_ or {}).items():
            if not values:
                return []
            q = q.in_(k, values)
        if order:
            q = q.order(order, desc=desc)
        if limit is not None:
            q = q.limit(limit)
        return self._run(q)

    def insert(self, table: str, rows: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._run(self.client.table(table).insert(rows))

    def update(self, table: str, values: dict[str, Any], *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if not eq:
            raise StoreError("refusing an unfiltered update")
        q = self.client.table(table).update(values)
        for k, v in eq.items():
            q = q.eq(k, v)
        return self._run(q)

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        try:
            return self.client.rpc(fn, params).execute().data
        except Exception as exc:
            raise StoreError(getattr(exc, "message", None) or str(exc)) from exc
