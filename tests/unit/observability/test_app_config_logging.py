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
    cfg = load_config_dict(_base_config({"logging": {"auto_configure": True, "level": "DEBUG", "pretty": True}}))
    assert isinstance(cfg.logging, LoggingConfig)
    assert cfg.logging.auto_configure is True
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.pretty is True


def test_invalid_level_rejected() -> None:
    import pytest

    with pytest.raises(Exception):
        load_config_dict(_base_config({"logging": {"level": "LOUD"}}))


def test_logging_accepts_loguru_sinks_via_dict() -> None:
    """Ensure YAML/dict roundtrip preserves the loguru_sinks list and its
    per-sink fields (rotation, serialize, colorize, etc.)."""
    from openagents.observability.config import LoguruSinkConfig

    payload = {
        "logging": {
            "level": "INFO",
            "pretty": False,
            "loguru_sinks": [
                {"target": "stderr", "colorize": True},
                {"target": ".logs/app.log", "rotation": "10 MB", "retention": "7 days"},
                {"target": ".logs/events.jsonl", "serialize": True, "enqueue": True},
            ],
        }
    }
    cfg = load_config_dict(_base_config(payload))
    assert isinstance(cfg.logging, LoggingConfig)
    assert len(cfg.logging.loguru_sinks) == 3
    assert all(isinstance(s, LoguruSinkConfig) for s in cfg.logging.loguru_sinks)
    assert cfg.logging.loguru_sinks[0].colorize is True
    assert cfg.logging.loguru_sinks[1].rotation == "10 MB"
    assert cfg.logging.loguru_sinks[1].retention == "7 days"
    assert cfg.logging.loguru_sinks[2].serialize is True
    assert cfg.logging.loguru_sinks[2].enqueue is True
