"""Shared fixtures. All data is synthetic (seed/data.py)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import LLMClient
from app.llm.base import Prompt
from app.llm.cache import MemoryCache
from app.llm.stub import OfflineStubProvider
from app.services.access import Actor
from app.store.memory import MemoryStore
from seed import data
from seed.data import sid
from seed.run import seed


@pytest.fixture
def settings() -> Settings:
    from redaction.crypto import generate_key

    return Settings(_env_file=None, llm_provider="stub", require_redaction_approval=False, supabase_jwt_secret="test-secret-for-hs256-only-0123456789",
                    redaction_key=generate_key())


@pytest.fixture
def store() -> MemoryStore:
    s = MemoryStore()
    seed(s)
    return s


@pytest.fixture
def stub_llm() -> LLMClient:
    return LLMClient(OfflineStubProvider(), cache=MemoryCache(), temperature=0.0)


@pytest.fixture
def officer(store: MemoryStore) -> Actor:
    uid = sid("user:officer")
    store.insert("profiles", {"id": uid, "role": "officer", "organisation_id": data.ORG_ID})
    return Actor(user_id=uid, role="officer", organisation_id=data.ORG_ID)


@pytest.fixture
def other_officer(store: MemoryStore) -> Actor:
    org = store.insert("organisations", {"name": "Another Fictional Office"})[0]
    uid = sid("user:other-officer")
    store.insert("profiles", {"id": uid, "role": "officer", "organisation_id": org["id"]})
    return Actor(user_id=uid, role="officer", organisation_id=org["id"])


@pytest.fixture
def applicant_actor(store: MemoryStore) -> Actor:
    """Owns case N01's applicant record."""
    uid = sid("user:applicant")
    store.insert("profiles", {"id": uid, "role": "applicant"})
    store.update("applicants", {"user_id": uid}, eq={"id": sid("applicant:N01")})
    return Actor(user_id=uid, role="applicant")


def app_id(code: str) -> str:
    return sid(f"application:{code}")


class ScriptedProvider:
    """Test double: returns whatever `respond(prompt, schema)` returns (str or Exception)."""

    name = "scripted"
    model = "scripted-v1"

    def __init__(self, respond: Callable[[Prompt, type[BaseModel]], Any]) -> None:
        self.respond = respond
        self.calls: list[Prompt] = []

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        self.calls.append(prompt)
        out = self.respond(prompt, schema)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)


@pytest.fixture
def scripted() -> Callable[..., tuple[LLMClient, ScriptedProvider]]:
    def make(respond: Callable[[Prompt, type[BaseModel]], Any], **kw: Any) -> tuple[LLMClient, ScriptedProvider]:
        p = ScriptedProvider(respond)
        return LLMClient(p, temperature=0.0, sleep=lambda _s: None, **kw), p

    return make


def fill_placeholders(body: str) -> str:
    """An officer replaces each [placeholder] (agency details not in the settings) with real wording. Quoted lines are left alone."""
    import re

    return "\n".join(line if line.startswith("What you wrote:") else re.sub(r"\[[^\[\]\n]{2,80}\]", "the details on our website", line)
                     for line in body.split("\n"))


def fill_and_read(store, officer, letter_id: str) -> dict:
    """Before any release the officer fills every placeholder and reads the letter to the end (migration 0019, outcomes brief)."""
    from app.services import letters

    letter = store.select("letters", eq={"id": letter_id})[0]
    letters.update_letter(store, officer, letter_id, body_text=fill_placeholders(letter["body_text"]))
    return letters.mark_viewed(store, officer, letter_id)
