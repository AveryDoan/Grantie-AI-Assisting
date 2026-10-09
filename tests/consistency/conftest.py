"""Fixtures for the consistency-layer tests. Every case is synthetic."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("presidio_analyzer")
pytest.importorskip("en_core_web_lg")

from app.config import Settings  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.llm.stub import OfflineStubProvider  # noqa: E402
from app.pipeline.consistency import ConsistencyContext  # noqa: E402
from app.pipeline.consistency.context import DocView  # noqa: E402
from app.pipeline.documents import extract_fields  # noqa: E402
from app.pipeline.orchestrator import run_assessment  # noqa: E402
from app.services.access import Actor  # noqa: E402
from app.store.memory import MemoryStore  # noqa: E402
from redaction.crypto import generate_key  # noqa: E402
from seed import data  # noqa: E402
from seed.fraud.load import seed_fraud  # noqa: E402
from seed.run import seed  # noqa: E402

HASH_KEY = "test-only-identifier-hash-key-0123456789abcdef"


def make_settings(**kw) -> Settings:
    return Settings(_env_file=None, llm_provider="stub", redaction_key=generate_key(), consistency_layer=True,
                    identifier_hash_key=HASH_KEY, supabase_jwt_secret="test-secret-for-hs256-only-0123456789", **kw)


def make_pool() -> tuple[MemoryStore, dict[str, str], Actor, Settings]:
    store = MemoryStore()
    seed(store)
    ids = seed_fraud(store)
    uid = data.sid("user:officer")
    store.insert("profiles", {"id": uid, "role": "officer", "organisation_id": data.ORG_ID})
    return store, ids, Actor(user_id=uid, role="officer", organisation_id=data.ORG_ID), make_settings()


def stub() -> LLMClient:
    return LLMClient(OfflineStubProvider(), temperature=0.0)


@pytest.fixture(scope="module")
def pool():
    """All ten synthetic cases and the clean control, assessed once with the offline stub."""
    store, ids, actor, settings = make_pool()
    ids["N08"] = data.sid("application:N08")
    for code in ids:
        run_assessment(store, actor, ids[code], stub(), settings)
    return {"store": store, "ids": ids, "actor": actor, "settings": settings}


def flags_of(pool, code: str) -> list[dict]:
    return pool["store"].select("consistency_flags", eq={"application_id": pool["ids"][code]})


def raised(pool, code: str, *, verified_only: bool = True) -> set[str]:
    return {f["check_id"] for f in flags_of(pool, code) if not verified_only or f["verification"] == "verified"}


# ---------------------------------------------------------------- hand-made contexts for unit tests


def doc(i: str, type_: str, text: str, **kw) -> DocView:
    return DocView(id=i, type=type_, declared=type_, label=kw.pop("label", type_), text=text, fields=extract_fields(text), **kw)


def ctx(docs: list[DocView], **kw) -> ConsistencyContext:
    return ConsistencyContext(app_id="a1", fields=kw.pop("fields", {}), answers=kw.pop("answers", {}),
                              submitted=kw.pop("submitted", date(2026, 10, 12)), docs=docs, **kw)
