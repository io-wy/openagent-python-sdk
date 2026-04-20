"""Config loader entrypoints."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..errors.exceptions import ConfigLoadError, ConfigValidationError
from .schema import AppConfig


def _expand_env_vars(text: str, *, source: Path | None = None) -> str:
    """Expand ${VAR} and ${VAR:-default} placeholders using environment variables."""

    def _replace(match: re.Match) -> str:
        expr = match.group(1)
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            return os.environ.get(var_name.strip(), default)
        value = os.environ.get(expr.strip())
        if value is None:
            if source is not None:
                hint = (
                    f"Set '{expr.strip()}' in your shell, or copy {source.parent}/.env.example to {source.parent}/.env"
                )
            else:
                hint = f"Set '{expr.strip()}' in your shell"
            raise ConfigLoadError(
                f"Environment variable '{expr.strip()}' is not set (referenced in config as ${{{expr.strip()}}})",
                hint=hint,
            )
        return value

    return re.sub(r"\$\{([^}]+)\}", _replace, text)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigLoadError(
            f"Config file does not exist: {config_path}",
            hint="Run from the repo root, or pass an absolute path to the config file",
            docs_url="docs/configuration.md",
        )
    if not config_path.is_file():
        raise ConfigLoadError(
            f"Config path is not a file: {config_path}",
            hint="Pass the path to a JSON file (e.g. agent.json), not a directory",
        )

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigLoadError(
            f"Failed to read config file: {config_path}",
            hint="Check file permissions and that the file is not locked by another process",
        ) from exc

    try:
        raw_text = _expand_env_vars(raw_text, source=config_path)
    except ConfigLoadError:
        raise
    except Exception as exc:
        raise ConfigLoadError(
            f"Failed to expand env vars in config: {config_path}",
            hint="Check that all ${VAR} placeholders use valid variable names",
        ) from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(
            f"Invalid JSON in config file: {config_path}",
            hint=f"Validate the JSON syntax (e.g. via 'jq . {config_path}'); see line {exc.lineno}, column {exc.colno}",
        ) from exc

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
