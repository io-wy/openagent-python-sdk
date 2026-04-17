"""Config loader entrypoints."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schema import AppConfig
from ..errors.exceptions import ConfigLoadError, ConfigValidationError


def _expand_env_vars(text: str) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders using environment variables."""
    def _replace(match: re.Match) -> str:
        expr = match.group(1)
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            return os.environ.get(var_name.strip(), default)
        value = os.environ.get(expr.strip())
        if value is None:
            raise ConfigLoadError(
                f"Environment variable '{expr.strip()}' is not set "
                f"(referenced in config as ${{{expr.strip()}}})"
            )
        return value

    return re.sub(r"\$\{([^}]+)\}", _replace, text)


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
        raw_text = _expand_env_vars(raw_text)
    except ConfigLoadError:
        raise
    except Exception as exc:
        raise ConfigLoadError(f"Failed to expand env vars in config: {config_path}") from exc

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

        loc = first_error.get("loc")
        if loc:
            location = ".".join(str(part) for part in loc)
            message = f"{location}: {message}"

        input_value = first_error.get("input")
        if input_value is not None:
            message = f"{message} (input={input_value!r})"

        input_type = first_error.get("input_type")
        if input_type:
            message = f"{message} (input_type={input_type})"
        raise ConfigValidationError(message) from exc

