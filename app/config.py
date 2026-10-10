"""Runtime configuration, read from environment variables (or a local .env).

Secrets live only in the environment. Nothing in this module logs them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "supabase" (default) or "demo": in-memory synthetic data + demo login, no Supabase needed.
    app_mode: Literal["supabase", "demo"] = "supabase"

    # --- Supabase -----------------------------------------------------------
    supabase_url: str = ""
    supabase_publishable_key: SecretStr = SecretStr("")
    # Server-side only: bypasses RLS. The API enforces access in code, and
    # the database triggers still apply.
    supabase_secret_key: SecretStr = SecretStr("")
    supabase_jwks_url: str = ""
    # Only needed for projects still on the legacy shared JWT secret.
    supabase_jwt_secret: SecretStr = SecretStr("")
    jwt_audience: str = "authenticated"

    # --- LLM ----------------------------------------------------------------
    llm_provider: Literal["gemini", "groq", "stub"] = "gemini"
    llm_fallback_provider: Literal["groq", "none"] = "none"
    gemini_api_key: SecretStr = SecretStr("")
    # Never hard-coded: model names and free-tier access change.
    gemini_model: str = ""
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = ""
    llm_temperature: float = Field(default=0.1, ge=0.0, le=0.2)
    llm_timeout_seconds: float = 30.0
    llm_max_rate_limit_retries: int = 4
    llm_backoff_base_seconds: float = 2.0
    llm_cache_enabled: bool = True
    # Days to keep cached LLM outputs; None keeps them until expires_at.
    llm_retention_days: int | None = 30

    # --- Pipeline -----------------------------------------------------------
    quote_fuzzy_threshold: float = Field(default=92.0, ge=50.0, le=100.0)
    quote_min_fuzzy_length: int = 12
    name_match_threshold: float = 85.0
    consistency_check: bool = False

    # --- Redaction -----------------------------------------------------------
    # AES-256-GCM key for token-map originals (base64, 32 bytes). Never logged.
    redaction_key: SecretStr = SecretStr("")
    redaction_key_previous: SecretStr = SecretStr("")

    # --- Consistency layer (consistency of information: signals, not findings) --------------
    # Off by default in supabase mode: it needs migration 0011. Demo mode turns it on.
    consistency_layer: bool = False
    # The AI may not read an application until an officer approves its redaction check (step 2 of the guided flow).
    require_redaction_approval: bool = True
    # HMAC key for the identifier hashes used to link applications (base64/url-safe, >= 32 bytes).
    # Identifiers are never stored or logged, only keyed hashes. Never commit this value.
    identifier_hash_key: SecretStr = SecretStr("")

    # --- API ----------------------------------------------------------------
    rate_limit_per_minute: int = 60
    rate_limit_assess_per_minute: int = 6
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("llm_retention_days", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
