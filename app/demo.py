"""DEMO MODE (APP_MODE=demo): run the whole product without Supabase.

- Data: synthetic seed in an in-memory store, optionally persisted to a local JSON snapshot.
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
    # A second fictional applicant, used to load the Sita Karki sample files through the real applicant upload flow.
    "sita": {"id": sid("user:sita"), "display_name": "Sita Karki (fictional)", "org": None},
}


def _demo_keys(snapshot: str):
    """`get(name, make)`: a demo key that lasts as long as the snapshot. Stored in <snapshot>.keys.json (local, gitignored)."""
    import json
    from pathlib import Path

    if not snapshot:
        return lambda _name, make: make()
    path = Path(f"{snapshot}.keys.json")
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(name: str, make) -> str:
        if name not in saved:
            saved[name] = make()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(saved), encoding="utf-8")
            path.chmod(0o600)
        return saved[name]

    return get


def build_demo(settings: Settings) -> tuple[MemoryStore, Settings, dict[str, dict]]:
    store = MemoryStore.load(settings.demo_data_path) if settings.demo_data_path else MemoryStore()
    if not store.tables:
        seed(store)
    for role, u in DEMO_USERS.items():
        if not store.select("profiles", eq={"id": u["id"]}):
            store.insert("profiles", {"permissions": ["can_view_round_scores", "can_record_round_decisions"] if role == "officer" else None, "id": u["id"], "role": "applicant" if role == "sita" else role, "organisation_id": u["org"], "display_name": u["display_name"]})
    store.update("applicants", {"user_id": DEMO_USERS["applicant"]["id"]}, eq={"id": sid("applicant:N01")})
    # The Study NT demo round is competitive: eligible applications wait for the panel's round decision (recorded by an officer).
    # Marks stay out of letters, and the number of places is left as a placeholder for the agency to fill.
    from seed.data import SNT_PACK

    for pack in store.select("rule_packs", eq={"id": SNT_PACK}):
        cfg = pack.get("letter_config") or {}
        if "competitive_round" not in cfg:
            store.update("rule_packs", {"letter_config": {**cfg, "competitive_round": True, "include_marks_in_letter": False}},
                         eq={"id": SNT_PACK})
    from redaction.crypto import generate_key

    update = {"supabase_jwt_secret": SecretStr(secrets.token_urlsafe(48)), "consistency_layer": True}
    # Without a snapshot the keys can be ephemeral. With one, the saved rows hold ciphertexts and keyed hashes made with these
    # keys, so a new key on restart would make them unreadable: keep them in a file beside the snapshot (never in it).
    keys = _demo_keys(settings.demo_data_path)
    if not settings.identifier_hash_key.get_secret_value():  # keyed hashes that link applications
        update["identifier_hash_key"] = SecretStr(keys("identifier_hash_key", lambda: secrets.token_urlsafe(32)))
    if not settings.redaction_key.get_secret_value():
        update["redaction_key"] = SecretStr(keys("redaction_key", generate_key))
    demo_settings = settings.model_copy(update=update)

    if not store.tables.get("evaluation_runs"):
        _seed_consistency_cases(store, demo_settings, DEMO_USERS["officer"])

        # Populate the evaluation dashboard (clearly labelled as the offline stub).
        from eval.harness import run_evaluation

        run_evaluation(store, LLMClient(OfflineStubProvider(), temperature=0.0), demo_settings,
                       provider_label="offline keyword stub (NOT an LLM)")
    store.persist()
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


def _seed_consistency_cases(store: MemoryStore, settings: Settings, officer: dict) -> None:
    """F01 to F10 (fictional, with deliberate inconsistencies), assessed with the offline stub so flags are ready to look at.

    The runs are labelled with the stub's model name. An officer can run the check again with the configured AI.
    """
    from app.pipeline.orchestrator import run_assessment
    from app.services.access import Actor
    from seed.fraud.load import seed_fraud

    ids = seed_fraud(store)
    actor = Actor(user_id=officer["id"], role="officer", organisation_id=officer["org"])
    stub = LLMClient(OfflineStubProvider(), temperature=0.0)
    for app_id in ids.values():
        try:
            run_assessment(store, actor, app_id, stub, settings)
        except Exception:  # the demo must start even if one case cannot be assessed
            pass
