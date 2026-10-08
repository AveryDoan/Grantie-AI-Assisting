"""DEMO MODE (APP_MODE=demo): run the whole product without Supabase.

- Data: the synthetic seed in an in-memory store (lost on restart).
- Login: POST /demo/login issues a short-lived token for a demo user, signed
  with a random secret generated at startup.
- LLM: the configured provider if it has a key, otherwise the offline keyword
  stub (NOT an LLM); every run records which one answered.

Never enable demo mode on a deployment that holds real data: anyone who can
reach /demo/login can sign in.
"""

from __future__ import annotations

import secrets
import time

import jwt
from pydantic import SecretStr

from app.config import Settings
from app.llm import LLMClient, LLMError, build_llm_client
from app.llm.cache import MemoryCache
from app.llm.stub import OfflineStubProvider
from app.store.memory import MemoryStore
from seed import data
from seed.data import sid
from seed.run import seed

DEMO_USERS = {
    "officer": {"id": sid("user:officer"), "display_name": "Demo Officer (fictional)", "org": data.ORG_ID},
    "admin": {"id": sid("user:admin"), "display_name": "Demo Admin (fictional)", "org": data.ORG_ID},
    "applicant": {"id": sid("user:applicant"), "display_name": "Demo Applicant (fictional)", "org": None},
}


def build_demo(settings: Settings) -> tuple[MemoryStore, Settings, dict[str, dict]]:
    store = MemoryStore()
    seed(store)
    for role, u in DEMO_USERS.items():
        store.insert("profiles", {"id": u["id"], "role": role, "organisation_id": u["org"], "display_name": u["display_name"]})
    store.update("applicants", {"user_id": DEMO_USERS["applicant"]["id"]}, eq={"id": sid("applicant:S01")})
    demo_settings = settings.model_copy(update={"supabase_jwt_secret": SecretStr(secrets.token_urlsafe(48))})

    # Populate the evaluation dashboard (clearly labelled as the offline stub).
    from eval.harness import run_evaluation

    run_evaluation(store, LLMClient(OfflineStubProvider(), temperature=0.0), demo_settings,
                   provider_label="offline keyword stub (NOT an LLM)")
    return store, demo_settings, DEMO_USERS


def demo_llm_factory(cache: MemoryCache):
    def make(_store: object, cfg: Settings) -> LLMClient:
        try:
            return build_llm_client(cfg, cache=cache)
        except LLMError:
            return LLMClient(OfflineStubProvider(), cache=cache, temperature=0.0)

    return make


def demo_token(settings: Settings, user_id: str, hours: int = 8) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "aud": settings.jwt_audience, "role": "authenticated", "iat": now, "exp": now + hours * 3600},
        settings.supabase_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
