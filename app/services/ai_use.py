"""AI-use check (sub-step 3.3). Filled in by part 2; part 1 only needs the count for the pill."""

from __future__ import annotations

from typing import Any

from app.store.base import Store


def open_count(store: Store, app: dict[str, Any]) -> dict[str, int]:
    return {"open": 0, "total": 0}
