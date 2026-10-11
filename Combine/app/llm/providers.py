"""Concrete providers: Gemini (default, google-genai SDK) and Groq (backup)."""

from __future__ import annotations

import json

import httpx
from pydantic import BaseModel

from app.llm.base import (
    LLMError,
    LLMInvalidOutput,
    LLMNotConfigured,
    LLMOverloaded,
    LLMRateLimited,
    LLMTimeout,
    Prompt,
    inline_json_schema,
)


class GeminiProvider:
    """Google Gemini via the official google-genai SDK with structured output."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMNotConfigured("GEMINI_API_KEY is not set")
        if not model:
            raise LLMNotConfigured("GEMINI_MODEL is not set (model names are config, never hard-coded)")
        from google import genai

        self.model = model
        self._client = genai.Client(api_key=api_key)

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=prompt.system,
            temperature=temperature,
            response_mime_type="application/json",
            response_json_schema=inline_json_schema(schema),
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt.user, config=config)
        except errors.APIError as exc:
            if exc.code == 429:
                raise LLMRateLimited("Gemini rate limit (429)") from None
            if exc.code in (500, 502, 503):
                raise LLMOverloaded(f"Gemini temporarily unavailable ({exc.code})") from None
            if exc.code in (408, 504):
                raise LLMTimeout(f"Gemini timeout ({exc.code})") from None
            raise LLMError(f"Gemini API error {exc.code}") from None
        except (httpx.TimeoutException, TimeoutError):
            raise LLMTimeout("Gemini request timed out") from None
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini transport error: {type(exc).__name__}") from None
        text = response.text
        if not text:
            raise LLMInvalidOutput("Gemini returned an empty response")
        return text


class GroqProvider:
    """Groq (OpenAI-compatible API) as a backup when Gemini is rate-limited."""

    name = "groq"
    _URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key or not model:
            raise LLMNotConfigured("GROQ_API_KEY and GROQ_MODEL must be set")
        self.model = model
        self._api_key = api_key

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        system = (
            prompt.system
            + "\n\nRespond with a single JSON object that validates against this JSON schema:\n"
            + json.dumps(inline_json_schema(schema))
        )
        body = {
            "model": self.model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt.user}],
        }
        try:
            r = httpx.post(
                self._URL, json=body, timeout=timeout, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.TimeoutException:
            raise LLMTimeout("Groq request timed out") from None
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq transport error: {type(exc).__name__}") from None
        if r.status_code == 429:
            raise LLMRateLimited("Groq rate limit (429)")
        if r.status_code in (500, 502, 503):
            raise LLMOverloaded(f"Groq temporarily unavailable ({r.status_code})")
        if r.status_code >= 400:
            raise LLMError(f"Groq API error {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            raise LLMInvalidOutput("Groq returned an unexpected payload") from None
