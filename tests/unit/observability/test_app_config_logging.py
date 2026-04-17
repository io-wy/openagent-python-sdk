"""Tests for AppConfig.logging field."""

from __future__ import annotations

from openagents.config.loader import load_config_dict
from openagents.observability.config import LoggingConfig


def _base_config(extras: dict | None = None) -> dict:
    base = {
        "version": "1.0",
        "agents": [
            {
                "id": "a1",
                "name": "A1",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
            }
        ],
    }
    if extras:
        base.update(extras)
    return base


def test_logging_defaults_to_none() -> None:
    cfg = load_config_dict(_base_config())
    assert cfg.logging is None


def test_logging_parsed_from_dict() -> None:
    cfg = load_config_dict(
        _base_config(
            {"logging": {"auto_configure": True, "level": "DEBUG", "pretty": True}}
        )
    )
    assert isinstance(cfg.logging, LoggingConfig)
    assert cfg.logging.auto_configure is True
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.pretty is True


def test_invalid_level_rejected() -> None:
    import pytest

    with pytest.raises(Exception):
        load_config_dict(_base_config({"logging": {"level": "LOUD"}}))
