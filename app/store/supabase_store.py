"""Store backed by Supabase (PostgREST) using the server-side secret key.

The secret key bypasses RLS, so every service function checks the caller's
role and organisation before touching data (see app.services.access).
Integrity triggers in the database still run and are surfaced as StoreError.

FastAPI runs sync endpoints in a thread pool. A single shared HTTP/2
connection is not safe across threads ("Server disconnected"), so each
thread gets its own client on a plain HTTP/1.1 connection. Read-only
queries are retried once on a dropped connection; writes are never retried.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from app.config import Settings
from app.store.base import StoreError

IN_CHUNK = 100  # keep PostgREST URLs a sensible length


class SupabaseStore:
    def __init__(self, settings: Settings) -> None:
        key = settings.supabase_secret_key.get_secret_value()
        if not settings.supabase_url or not key:
            raise StoreError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set")
        self._url = settings.supabase_url
        self._key = key
        self._local = threading.local()

    @property
    def client(self) -> Any:
        client = getattr(self._local, "client", None)
        if client is None:
            from supabase import ClientOptions, create_client  # imported lazily: tests don't need it

            client = create_client(
                self._url,
                self._key,
                options=ClientOptions(httpx_client=httpx.Client(http2=False, timeout=30.0)),
            )
            self._local.client = client
        return client

    def _run(self, build: Any, *, retry: bool) -> list[dict[str, Any]]:
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                return build().execute().data or []
            except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
                if attempt + 1 < attempts:
                    self._local.client = None  # fresh connection, then retry the read
                    continue
                raise StoreError(f"Database connection problem: {type(exc).__name__}") from exc
            except Exception as exc:  # postgrest.APIError and friends
                message = getattr(exc, "message", None) or str(exc)
                if retry and attempt + 1 < attempts and "disconnected" in message.lower():
                    self._local.client = None
                    continue
                raise StoreError(message) from exc
        return []

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
        in_ = in_ or {}
        for k, values in in_.items():
            if not values:
                return []
            if len(values) > IN_CHUNK:
                # Split one large IN list into several requests and merge.
                rows: list[dict[str, Any]] = []
                for i in range(0, len(values), IN_CHUNK):
                    rows += self.select(table, eq=eq, in_={**in_, k: values[i : i + IN_CHUNK]})
                if order:
                    rows.sort(key=lambda r: (r.get(order) is None, r.get(order)), reverse=desc)
                return rows[:limit] if limit is not None else rows

        def build() -> Any:
            q = self.client.table(table).select("*")
            for k, v in (eq or {}).items():
                q = q.is_(k, "null") if v is None else q.eq(k, v)
            for k, values in in_.items():
                q = q.in_(k, values)
            if order:
                q = q.order(order, desc=desc)
            if limit is not None:
                q = q.limit(limit)
            return q

        return self._run(build, retry=True)

    def insert(self, table: str, rows: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._run(lambda: self.client.table(table).insert(rows), retry=False)

    def update(self, table: str, values: dict[str, Any], *, eq: dict[str, Any]) -> list[dict[str, Any]]:
        if not eq:
            raise StoreError("refusing an unfiltered update")

        def build() -> Any:
            q = self.client.table(table).update(values)
            for k, v in eq.items():
                q = q.eq(k, v)
            return q

        return self._run(build, retry=False)

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        try:
            return self.client.rpc(fn, params).execute().data
        except Exception as exc:
            raise StoreError(getattr(exc, "message", None) or str(exc)) from exc
