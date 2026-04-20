"""Integration tests for Runtime auto-configure hook."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from openagents.runtime.runtime import Runtime


def _base_config(logging_section: dict | None = None) -> dict:
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
    if logging_section is not None:
        base["logging"] = logging_section
    return base


def _reset_openagents_logger() -> None:
    from openagents.observability.logging import reset_logging

    reset_logging()


@pytest.fixture(autouse=True)
def _reset_around() -> None:
    _reset_openagents_logger()
    yield
    _reset_openagents_logger()


def test_auto_configure_true_calls_configure() -> None:
    with patch("openagents.observability.logging.configure", autospec=True) as mock_configure:
        Runtime.from_dict(_base_config({"auto_configure": True, "level": "DEBUG"}))
    assert mock_configure.call_count == 1
    cfg = mock_configure.call_args.args[0]
    assert cfg.level == "DEBUG"


def test_auto_configure_false_does_not_call_configure() -> None:
    with patch("openagents.observability.logging.configure", autospec=True) as mock_configure:
        Runtime.from_dict(_base_config({"auto_configure": False}))
    assert mock_configure.call_count == 0


def test_no_logging_section_does_not_call_configure() -> None:
    with patch("openagents.observability.logging.configure", autospec=True) as mock_configure:
        Runtime.from_dict(_base_config())
    assert mock_configure.call_count == 0


def test_auto_configure_actually_sets_level_end_to_end() -> None:
    Runtime.from_dict(_base_config({"auto_configure": True, "level": "DEBUG"}))
    assert logging.getLogger("openagents").level == logging.DEBUG
