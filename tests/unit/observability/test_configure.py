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
        configure(LoggingConfig(level="INFO", per_logger_levels={"openagents.llm": "DEBUG"}))
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
        configure(LoggingConfig(level="INFO", per_logger_levels={"openagents.llm": "DEBUG"}))
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

    def test_ignores_per_logger_levels_outside_openagents(self, caplog: pytest.LogCaptureFixture) -> None:
        configure(LoggingConfig(per_logger_levels={"openagents.llm": "DEBUG", "third_party": "DEBUG"}))
        assert any("third_party" in rec.message and "ignored" in rec.message for rec in caplog.records)


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


class TestConfigureLoguruBranch:
    """Task 11: loguru_sinks routes handler install through
    _LoguruInterceptHandler; OPENAGENTS_LOG_LOGURU_DISABLE downgrades with
    a WARNING."""

    def test_loguru_sinks_installs_intercept_handler(self) -> None:
        pytest.importorskip("loguru")
        from openagents.observability._loguru import (
            _INSTALLED_SINK_IDS,
            _LoguruInterceptHandler,
        )

        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert isinstance(handlers[0], _LoguruInterceptHandler)
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_disable_env_downgrades_to_stream_handler_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec test 8.

        We attach a probe handler directly to ``openagents.observability.logging``
        because the openagents tree has ``propagate=False`` after configure(),
        so pytest's ``caplog`` (which attaches to the root logger) never sees
        records emitted by the observability module.
        """
        pytest.importorskip("loguru")
        from openagents.observability._loguru import _LoguruInterceptHandler

        captured: list[logging.LogRecord] = []

        class _Probe(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        probe = _Probe()
        probe.setLevel(logging.WARNING)
        obs_logger = logging.getLogger("openagents.observability.logging")
        obs_logger.addHandler(probe)
        try:
            monkeypatch.setenv("OPENAGENTS_LOG_LOGURU_DISABLE", "1")
            configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        finally:
            obs_logger.removeHandler(probe)

        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert not isinstance(handlers[0], _LoguruInterceptHandler)
        assert any("OPENAGENTS_LOG_LOGURU_DISABLE" in r.getMessage() for r in captured)

    def test_configure_plain_branch_still_works(self) -> None:
        configure(LoggingConfig(pretty=False, level="INFO"))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert handlers[0].__class__.__name__ == "StreamHandler"


class TestResetLoggingLoguruCleanup:
    """Task 12: reset_logging() clears loguru sinks; configure() rollback
    on filter-wiring failure."""

    def test_reset_clears_installed_sink_ids(self) -> None:
        pytest.importorskip("loguru")
        from openagents.observability._loguru import _INSTALLED_SINK_IDS

        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        assert len(_INSTALLED_SINK_IDS) == 1
        reset_logging()
        assert _INSTALLED_SINK_IDS == []

    def test_repeated_configure_replaces_sinks_not_stacks(self) -> None:
        """Spec test 5."""
        pytest.importorskip("loguru")
        from openagents.observability._loguru import _INSTALLED_SINK_IDS

        configure(
            LoggingConfig(
                loguru_sinks=[
                    {"target": "stderr"},
                    {"target": "stdout"},
                ]
            )
        )
        assert len(_INSTALLED_SINK_IDS) == 2
        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_reset_idempotent(self) -> None:
        reset_logging()
        reset_logging()  # must not raise


class TestLoguruImportFallbacks:
    """Cover the ImportError-guarded paths in reset_logging() and configure()'s
    rollback. These run when loguru is not installed; we simulate that by
    blocking ``openagents.observability._loguru`` in sys.modules."""

    def test_reset_logging_silent_when_loguru_module_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Block the module import: setitem to None makes `from X import Y` raise ImportError
        monkeypatch.setitem(sys.modules, "openagents.observability._loguru", None)
        # configure() with the plain branch (no loguru_sinks) so we don't hit the install path
        configure(LoggingConfig(level="INFO"))
        # reset_logging hits the ImportError branch and returns silently
        reset_logging()
        # Subsequent reset_logging calls also stay silent
        reset_logging()

    def test_configure_rollback_silent_when_loguru_module_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Filter wiring fails AND loguru module is unavailable — rollback still
        completes without raising ImportError."""
        # Pre-condition: configure with plain branch so reset_logging will be triggered
        # via the rollback path.
        import openagents.observability.logging as log_mod

        monkeypatch.setitem(sys.modules, "openagents.observability._loguru", None)
        orig_prefix = log_mod.PrefixFilter

        class BoomFilter(orig_prefix):
            def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("simulated filter failure")

        monkeypatch.setattr(log_mod, "PrefixFilter", BoomFilter)

        with pytest.raises(RuntimeError, match="simulated filter failure"):
            configure(LoggingConfig(level="INFO"))
        # No second exception masking the original


class TestConfigureRollback:
    def test_filter_construction_failure_rolls_back_loguru_sinks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Load-bearing invariant from spec §4.2: configure() rollback
        restores _INSTALLED_SINK_IDS to empty when filter wiring fails."""
        pytest.importorskip("loguru")
        import openagents.observability.logging as log_mod
        from openagents.observability._loguru import _INSTALLED_SINK_IDS

        orig_prefix = log_mod.PrefixFilter

        class BoomFilter(orig_prefix):
            def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("simulated filter failure")

        monkeypatch.setattr(log_mod, "PrefixFilter", BoomFilter)

        with pytest.raises(RuntimeError, match="simulated filter failure"):
            configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        assert _INSTALLED_SINK_IDS == []
