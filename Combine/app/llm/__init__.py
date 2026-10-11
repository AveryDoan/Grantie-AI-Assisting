"""LLM access. Everything goes through `call_llm(prompt, schema)`."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.llm.base import (
    LLMError,
    LLMInvalidOutput,
    LLMNotConfigured,
    LLMOverloaded,
    LLMRateLimited,
    LLMTimeout,
    Prompt,
)
from app.llm.client import LLMClient, build_llm_client

T = TypeVar("T", bound=BaseModel)

_default: LLMClient | None = None


def set_default_client(client: LLMClient | None) -> None:
    global _default
    _default = client


def call_llm(prompt: Prompt, schema: type[T]) -> T:
    """Validated structured output from the configured provider, or LLMError."""
    global _default
    if _default is None:
        from app.config import get_settings

        _default = build_llm_client(get_settings())
    return _default.call_llm(prompt, schema)


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMInvalidOutput",
    "LLMNotConfigured",
    "LLMOverloaded",
    "LLMRateLimited",
    "LLMTimeout",
    "Prompt",
    "build_llm_client",
    "call_llm",
    "set_default_client",
]
