"""Config loader entrypoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schema import AppConfig
from ..errors.exceptions import ConfigLoadError, ConfigValidationError


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigLoadError(f"Config file does not exist: {config_path}")
    if not config_path.is_file():
        raise ConfigLoadError(f"Config path is not a file: {config_path}")

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(f"Failed to read config file: {config_path}") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"Invalid JSON in config file: {config_path}") from exc

    return load_config_dict(payload)


def load_config_dict(payload: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        message = first_error.get("msg", str(exc))
        raise ConfigValidationError(message) from exc

