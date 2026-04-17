"""Integration tests for OPENAGENTS_LOG_* env var overrides."""

from __future__ import annotations

import logging

import pytest

from openagents.runtime.runtime import Runtime


def _base_config(logging_section: dict) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "a1",
                "name": "A1",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
            }
        ],
        "logging": logging_section,
    }


@pytest.fixture(autouse=True)
def _reset_around() -> None:
    from openagents.observability.logging import reset_logging

    reset_logging()
    yield
    reset_logging()


def test_env_overrides_file_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "WARNING")
    Runtime.from_dict(
        _base_config({"auto_configure": True, "level": "DEBUG"})
    )
    assert logging.getLogger("openagents").level == logging.WARNING


def test_env_autoconfigure_activates_without_file_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENTS_LOG_AUTOCONFIGURE", "1")
    monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "DEBUG")
    Runtime.from_dict(_base_config({"auto_configure": False}))
    assert logging.getLogger("openagents").level == logging.DEBUG


def test_unset_env_does_not_clobber(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in [
        "OPENAGENTS_LOG_AUTOCONFIGURE",
        "OPENAGENTS_LOG_LEVEL",
        "OPENAGENTS_LOG_PRETTY",
    ]:
        monkeypatch.delenv(var, raising=False)
    Runtime.from_dict(
        _base_config({"auto_configure": True, "level": "DEBUG"})
    )
    assert logging.getLogger("openagents").level == logging.DEBUG
