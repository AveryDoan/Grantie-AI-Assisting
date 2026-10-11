"""Load and validate config/redaction.yaml.

The config hash (SHA-256 of the file bytes) is stored on every redaction run,
so any change to patterns, thresholds or allowlists is traceable.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "redaction.yaml"

TokenType = str  # PERSON, REFEREE, EMAIL, PHONE, ADDRESS, PLACE, DOB, ID, URL, HANDLE


class EntitySetting(BaseModel):
    token: TokenType | None
    min_score: float = Field(ge=0.0, le=1.0)


class PatternSpec(BaseModel):
    pattern: str
    score: float | None = None


class PatternGroup(BaseModel):
    regex: list[PatternSpec]
    context: list[str] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("regex", mode="before")
    @classmethod
    def _strings_to_specs(cls, v: Any) -> Any:
        return [{"pattern": x} if isinstance(x, str) else x for x in v]

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: list[PatternSpec]) -> list[PatternSpec]:
        for spec in v:
            re.compile(spec.pattern)
        return v


class DobContext(BaseModel):
    window: int = 40
    cues: list[str]


class NameCues(BaseModel):
    honorifics: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class DenyEntry(BaseModel):
    value: str
    type: TokenType


class RedactionConfig(BaseModel):
    version: int
    detector: dict[str, str]
    token_format: str
    default_min_score: float
    flag_score: float
    low_confidence_action: Literal["redact", "block"]
    entities: dict[str, EntitySetting]
    patterns: dict[str, PatternGroup]
    phone_regions: list[str]
    dob_context: DobContext
    name_cues: NameCues = Field(default_factory=NameCues)
    structured_fields: dict[TokenType, list[str]]
    person_groups: dict[str, list[str]] = Field(default_factory=dict)
    location_fields: dict[str, list[str]]
    allowlist: list[str]
    denylist: list[DenyEntry] = Field(default_factory=list)
    countries: list[str]
    config_hash: str = ""

    # ---- derived helpers ---------------------------------------------------
    def token_type(self, entity: str) -> TokenType | None:
        setting = self.entities.get(entity)
        return setting.token if setting else None

    def min_score(self, entity: str) -> float:
        setting = self.entities.get(entity)
        return setting.min_score if setting else self.default_min_score

    def field_token_type(self, field_name: str) -> TokenType | None:
        for token_type, names in self.structured_fields.items():
            if field_name in names:
                return token_type
        return None

    def person_group(self, field_name: str) -> str | None:
        for group, names in self.person_groups.items():
            if field_name in names:
                return group
        return None

    def allowlist_patterns(self) -> list[re.Pattern[str]]:
        out = []
        for entry in self.allowlist:
            if entry.startswith("re:"):
                out.append(re.compile(entry[3:], re.IGNORECASE))
            else:
                out.append(re.compile(rf"(?<!\w){re.escape(entry)}(?!\w)", re.IGNORECASE))
        return out

    def country_set(self) -> frozenset[str]:
        return frozenset(c.casefold() for c in self.countries)

    def format_token(self, token_type: TokenType, n: int) -> str:
        return self.token_format.format(type=token_type, n=n)


def load_config(path: Path | str | None = None) -> RedactionConfig:
    p = Path(path) if path else DEFAULT_PATH
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    cfg = RedactionConfig.model_validate(data)
    cfg.config_hash = hashlib.sha256(raw).hexdigest()
    unknown = [k for k in cfg.patterns if k not in cfg.entities and k != "INTERNATIONAL_PHONE"]
    if unknown:
        raise ValueError(f"patterns defined for entities with no token setting: {unknown}")
    return cfg


@lru_cache
def default_config() -> RedactionConfig:
    return load_config()
