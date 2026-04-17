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

    def test_reset_restores_level_and_propagate(self) -> None:
        configure(LoggingConfig(level="DEBUG"))
        reset_logging()
        root = logging.getLogger("openagents")
        assert root.level == logging.NOTSET
        assert root.propagate is True

    def test_reset_restores_per_logger_levels(self) -> None:
        configure(
            LoggingConfig(level="INFO", per_logger_levels={"openagents.llm": "DEBUG"})
        )
        assert logging.getLogger("openagents.llm").level == logging.DEBUG
        reset_logging()
        assert logging.getLogger("openagents.llm").level == logging.NOTSET


class TestPerLoggerLevelsEndToEnd:
    """Per-logger level overrides must actually produce output, not just
    satisfy an in-isolation filter check. Python's logger gate drops DEBUG
    records before they reach handler-side filters unless the named logger
    itself has a low-enough level. This test attaches a probe handler to
    capture records directly (caplog can't see through propagate=False).
    """

    def test_per_logger_debug_bypasses_root_info_gate(self) -> None:
        configure(
            LoggingConfig(level="INFO", per_logger_levels={"openagents.llm": "DEBUG"})
        )
        captured: list[logging.LogRecord] = []

        class _Probe(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        probe = _Probe()
        probe.setLevel(logging.DEBUG)
        logging.getLogger("openagents").addHandler(probe)
        try:
            logging.getLogger("openagents.llm.anthropic").debug("reached handler")
            logging.getLogger("openagents.events").debug("dropped at source")
        finally:
            logging.getLogger("openagents").removeHandler(probe)

        debug_records = [r for r in captured if r.levelno == logging.DEBUG]
        assert any(r.name == "openagents.llm.anthropic" for r in debug_records)
        assert not any(r.name == "openagents.events" for r in debug_records)


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
