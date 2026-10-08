"""LLM provider contract and error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Prompt:
    """One structured LLM request.

    `user` already contains the application text wrapped in delimiters.
    `cache_text` + `rule_pack_version` + `prompt_version` + model + `task`
    form the cache key. `meta` is for the offline stub only and is never sent
    to a real provider.
    """

    task: str  # e.g. "facts", "rule:R7", "evidence:C6"
    system: str
    user: str
    prompt_version: str
    cache_text: str
    rule_pack_version: str
    meta: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


class LLMError(RuntimeError):
    """Any LLM failure. Messages must never contain application text."""


class LLMNotConfigured(LLMError):
    pass


class LLMRateLimited(LLMError):
    pass


class LLMTimeout(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


class Provider(Protocol):
    name: str
    model: str

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        """Return raw JSON text. Raise LLMRateLimited / LLMTimeout / LLMError."""
        ...


def inline_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $refs inlined (provider schema support varies)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
