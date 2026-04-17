"""LoggingConfig pydantic model and env-var parser."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_VALID_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def _normalize_level(value: str) -> str:
    up = value.upper()
    if up not in _VALID_LEVELS:
        raise ValueError(f"invalid log level: {value!r}")
    return up


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_configure: bool = False
    level: str = "INFO"
    per_logger_levels: dict[str, str] = Field(default_factory=dict)
    pretty: bool = False
    stream: Literal["stdout", "stderr"] = "stderr"
    include_prefixes: list[str] | None = None
    exclude_prefixes: list[str] = Field(default_factory=list)
    redact_keys: list[str] = Field(
        default_factory=lambda: [
            "api_key",
            "authorization",
            "token",
            "secret",
            "password",
        ]
    )
    max_value_length: int = 500
    show_time: bool = True
    show_path: bool = False

    @field_validator("level", mode="before")
    @classmethod
    def _v_level(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("level must be a string")
        return _normalize_level(value)

    @field_validator("per_logger_levels", mode="before")
    @classmethod
    def _v_per_logger(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("per_logger_levels must be a mapping")
        return {str(k): _normalize_level(str(v)) for k, v in value.items()}


_FIELD_ENV_MAP = {
    "auto_configure": "OPENAGENTS_LOG_AUTOCONFIGURE",
    "level": "OPENAGENTS_LOG_LEVEL",
    "per_logger_levels": "OPENAGENTS_LOG_LEVELS",
    "pretty": "OPENAGENTS_LOG_PRETTY",
    "stream": "OPENAGENTS_LOG_STREAM",
    "include_prefixes": "OPENAGENTS_LOG_INCLUDE",
    "exclude_prefixes": "OPENAGENTS_LOG_EXCLUDE",
    "redact_keys": "OPENAGENTS_LOG_REDACT",
    "max_value_length": "OPENAGENTS_LOG_MAX_VALUE_LENGTH",
}


def _env_value(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _env_overrides() -> dict[str, Any]:
    """Return only those fields whose env vars are set (non-empty)."""
    overrides: dict[str, Any] = {}
    for field, env_name in _FIELD_ENV_MAP.items():
        raw = _env_value(env_name)
        if raw is None:
            continue
        if field in {"auto_configure", "pretty"}:
            overrides[field] = raw.lower() in {"1", "true", "yes", "on"}
        elif field == "per_logger_levels":
            pairs: dict[str, str] = {}
            for part in raw.split(","):
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                pairs[k.strip()] = v.strip()
            overrides[field] = pairs
        elif field in {"include_prefixes", "exclude_prefixes", "redact_keys"}:
            overrides[field] = [p.strip() for p in raw.split(",") if p.strip()]
        elif field == "max_value_length":
            overrides[field] = int(raw)
        else:
            overrides[field] = raw
    return overrides


def load_from_env() -> LoggingConfig | None:
    """Return a LoggingConfig built entirely from env vars, or None if none set."""
    overrides = _env_overrides()
    if not overrides:
        return None
    return LoggingConfig(**overrides)


def merge_env_overrides(base: LoggingConfig) -> LoggingConfig:
    """Return a copy of base with env-var overrides applied (unset vars don't override)."""
    overrides = _env_overrides()
    if not overrides:
        return base
    merged = base.model_dump()
    merged.update(overrides)
    return LoggingConfig(**merged)
