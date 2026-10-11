"""Data access.

The services talk to a small table-oriented `Store` interface so the same
logic runs against Supabase (production) and an in-memory store (tests and
the offline evaluation). The database repeats the critical rules as
triggers; the services enforce them too.
"""

from app.store.base import Store, StoreError, now_iso
from app.store.memory import MemoryStore

__all__ = ["MemoryStore", "Store", "StoreError", "now_iso"]
