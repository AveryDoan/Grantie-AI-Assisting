from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol


class StoreError(RuntimeError):
    """A write or read was refused (constraint, trigger, permission, network)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store(Protocol):
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
        """Rows matching all filters. `eq` with a None value means IS NULL."""
        ...

    def insert(self, table: str, rows: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def update(self, table: str, values: dict[str, Any], *, eq: dict[str, Any]) -> list[dict[str, Any]]: ...

    def rpc(self, fn: str, params: dict[str, Any]) -> Any: ...


def one(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if rows else None
