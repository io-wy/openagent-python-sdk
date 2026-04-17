"""Tests for observability.logging (configure/reset)."""

from __future__ import annotations

import logging
import sys

import pytest

from openagents.observability import (
    LoggingConfig,
    RichNotInstalledError,
    configure,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _reset_before_and_after() -> None:
    reset_logging()
    yield
    reset_logging()


def _installed_handlers() -> list[logging.Handler]:
    root = logging.getLogger("openagents")
    return [h for h in root.handlers if getattr(h, "_openagents_installed", False)]


class TestConfigureBasic:
    def test_adds_stream_handler_when_pretty_false(self) -> None:
        configure(LoggingConfig(pretty=False, level="DEBUG"))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)

    def test_sets_openagents_logger_level(self) -> None:
        configure(LoggingConfig(level="DEBUG"))
        assert logging.getLogger("openagents").level == logging.DEBUG


class TestIdempotence:
    def test_repeated_calls_replace_handlers(self) -> None:
        configure(LoggingConfig(level="INFO"))
        configure(LoggingConfig(level="WARNING"))
        handlers = _installed_handlers()
        assert len(handlers) == 1  # not stacked

    def test_reset_removes_all_installed_handlers(self) -> None:
        configure(LoggingConfig())
        reset_logging()
        assert _installed_handlers() == []


class TestNamespaceIsolation:
    def test_does_not_touch_root_logger(self) -> None:
        root_before = list(logging.getLogger().handlers)
        configure(LoggingConfig())
        root_after = list(logging.getLogger().handlers)
        assert root_before == root_after

    def test_ignores_per_logger_levels_outside_openagents(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        configure(
            LoggingConfig(
                per_logger_levels={"openagents.llm": "DEBUG", "third_party": "DEBUG"}
            )
        )
        assert any(
            "third_party" in rec.message and "ignored" in rec.message for rec in caplog.records
        )


class TestPrettyGuard:
    def test_pretty_without_rich_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "rich", None)
        with pytest.raises(RichNotInstalledError):
            configure(LoggingConfig(pretty=True))

    def test_pretty_false_without_rich_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "rich", None)
        configure(LoggingConfig(pretty=False))  # does not raise
        assert len(_installed_handlers()) == 1


class TestThirdPartyHandlersUntouched:
    def test_only_tagged_handlers_removed(self) -> None:
        third_party = logging.StreamHandler()
        logging.getLogger("openagents").addHandler(third_party)
        configure(LoggingConfig())
        configure(LoggingConfig())  # second call triggers reset path
        assert third_party in logging.getLogger("openagents").handlers
        logging.getLogger("openagents").removeHandler(third_party)


class TestConfigureFromEnv:
    def test_configure_from_env_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openagents.observability import configure_from_env

        monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("OPENAGENTS_LOG_PRETTY", "0")
        configure_from_env()
        assert logging.getLogger("openagents").level == logging.DEBUG
