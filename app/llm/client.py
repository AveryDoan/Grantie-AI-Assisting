"""The ONE LLM interface: call_llm(prompt, schema) -> validated Pydantic object.

Behaviour:
- cache first (keyed by application text, rule pack version, prompt version,
  model, task);
- structured output from the provider, then validated again with Pydantic;
  one retry on invalid output;
- exponential backoff with jitter on 429; one retry on timeout;
- if the primary provider stays rate-limited and a fallback is configured,
  the fallback is tried;
- any remaining failure raises LLMError. Callers turn that into an "Unclear"
  finding with error_flag = true; they never default to "Met".
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.llm.base import LLMError, LLMInvalidOutput, LLMRateLimited, LLMTimeout, Prompt, Provider
from app.llm.cache import Cache, cache_key
from app.logging_utils import get_logger

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)


class LLMClient:
    def __init__(
        self,
        primary: Provider,
        *,
        fallback: Provider | None = None,
        cache: Cache | None = None,
        temperature: float = 0.1,
        timeout: float = 30.0,
        max_rate_limit_retries: int = 4,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0.0 <= temperature <= 0.2:
            raise ValueError("assessment temperature must be between 0 and 0.2")
        self.primary = primary
        self.fallback = fallback
        self.cache = cache
        self.temperature = temperature
        self.timeout = timeout
        self.max_rate_limit_retries = max_rate_limit_retries
        self.backoff_base = backoff_base
        self.sleep = sleep
        self.stats = {"calls": 0, "cache_hits": 0, "failures": 0}

    @property
    def model_name(self) -> str:
        return f"{self.primary.name}:{self.primary.model}"

    def call_llm(self, prompt: Prompt, schema: type[T]) -> T:
        providers = [self.primary] + ([self.fallback] if self.fallback else [])
        if self.cache:
            for p in providers:
                hit = self.cache.get(cache_key(prompt, p.model))
                if hit is not None:
                    try:
                        self.stats["cache_hits"] += 1
                        return schema.model_validate(hit)
                    except ValidationError:
                        pass  # schema changed since caching; fall through to a fresh call

        last: LLMError | None = None
        for p in providers:
            try:
                result = self._call_provider(p, prompt, schema)
            except LLMRateLimited as exc:
                last = exc
                log.warning("provider %s rate-limited for task %s; trying fallback if any", p.name, prompt.task)
                continue
            except LLMError as exc:
                self.stats["failures"] += 1
                raise exc
            if self.cache:
                self.cache.put(
                    cache_key(prompt, p.model), result.model_dump(mode="json"), provider=p.name, model=p.model, prompt=prompt
                )
            return result
        self.stats["failures"] += 1
        raise last or LLMError("no LLM provider available")

    def _call_provider(self, provider: Provider, prompt: Prompt, schema: type[T]) -> T:
        rate_limit_attempts = 0
        timeout_retries = 0
        invalid_retries = 0
        while True:
            self.stats["calls"] += 1
            try:
                raw = provider.generate(prompt, schema, temperature=self.temperature, timeout=self.timeout)
            except LLMRateLimited:
                if rate_limit_attempts >= self.max_rate_limit_retries:
                    raise
                delay = self.backoff_base * (2**rate_limit_attempts) + random.uniform(0, 1)
                rate_limit_attempts += 1
                log.info("rate limited (%s); backing off %.1fs", provider.name, delay)
                self.sleep(delay)
                continue
            except LLMTimeout:
                if timeout_retries >= 1:
                    raise
                timeout_retries += 1
                continue
            try:
                return schema.model_validate_json(raw)
            except ValidationError:
                if invalid_retries >= 1:
                    raise LLMInvalidOutput(
                        f"{provider.name} output failed schema validation twice for task {prompt.task}"
                    ) from None
                invalid_retries += 1
                log.info("invalid structured output for task %s; retrying once", prompt.task)


def build_llm_client(settings: Settings, cache: Cache | None = None) -> LLMClient:
    """Construct the configured client. Raises LLMNotConfigured if keys are missing."""
    from app.llm.providers import GeminiProvider, GroqProvider
    from app.llm.stub import OfflineStubProvider

    def make(name: str) -> Provider:
        if name == "gemini":
            return GeminiProvider(settings.gemini_api_key.get_secret_value(), settings.gemini_model)
        if name == "groq":
            return GroqProvider(settings.groq_api_key.get_secret_value(), settings.groq_model)
        if name == "stub":
            return OfflineStubProvider()
        raise LLMError(f"unknown provider {name}")

    primary = make(settings.llm_provider)
    fallback = None
    if settings.llm_fallback_provider != "none" and settings.llm_fallback_provider != settings.llm_provider:
        try:
            fallback = make(settings.llm_fallback_provider)
        except LLMError:
            fallback = None
    return LLMClient(
        primary,
        fallback=fallback,
        cache=cache if settings.llm_cache_enabled else None,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_rate_limit_retries=settings.llm_max_rate_limit_retries,
        backoff_base=settings.llm_backoff_base_seconds,
    )
