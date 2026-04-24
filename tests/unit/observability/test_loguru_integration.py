"""Integration tests for the loguru intercept handler.

Requires the [loguru] extra. Module skipped cleanly if loguru is absent.
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("loguru")

from openagents.observability._loguru import (  # noqa: E402
    _INSTALLED_SINK_IDS,
    _LoguruInterceptHandler,
    _sink_filter,
    install_sinks,
    remove_installed_sinks,
)


@pytest.fixture(autouse=True)
def _reset_loguru_state():
    """Clean both openagents and global loguru state around every test.

    Test-only concession: ``loguru.logger.remove()`` (no args) is called here
    to wipe loguru's default stderr sink (ID 0, installed at import time) as
    well as any leaked sinks from failed tests. Production code MUST NOT do
    this — it would clear sinks the user's app installed. Tests own the
    process and are free to do it.

    We also clear the stdlib handler chain on ``openagents.test`` so the
    ``_build_handler_with_sink`` helper starts from a clean logger.
    """
    from loguru import logger as _lg

    from openagents.observability import reset_logging

    _lg.remove()  # drop loguru default sink + any leftover sinks
    _INSTALLED_SINK_IDS.clear()
    reset_logging()
    _clear_openagents_test_logger()
    yield
    _lg.remove()
    _INSTALLED_SINK_IDS.clear()
    reset_logging()
    _clear_openagents_test_logger()


def _clear_openagents_test_logger() -> None:
    lg = logging.getLogger("openagents.test")
    for h in list(lg.handlers):
        lg.removeHandler(h)
    lg.setLevel(logging.NOTSET)


# ---------------------------------------------------------------------------
# Task 7: _require_loguru / _sink_filter / module state
# ---------------------------------------------------------------------------


class TestRequireLoguru:
    def test_returns_loguru_logger(self):
        from openagents.observability._loguru import _require_loguru

        lg = _require_loguru()
        # loguru.logger is a singleton exposing level/bind/add/remove
        assert hasattr(lg, "add")
        assert hasattr(lg, "remove")
        assert hasattr(lg, "bind")


class TestSinkFilter:
    def test_rejects_record_without_openagents_tag(self):
        f = _sink_filter(None)
        assert f({"extra": {}}) is False
        assert f({"extra": {"_openagents": False}}) is False

    def test_accepts_tagged_record_no_include(self):
        f = _sink_filter(None)
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is True

    def test_filter_include_prefix_match(self):
        f = _sink_filter(["openagents.llm"])
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is True
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm.anthropic"}}) is True
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.runtime"}}) is False

    def test_filter_include_empty_list_matches_nothing(self):
        f = _sink_filter([])
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is False


class TestInstalledSinkIdsModuleState:
    def test_initially_empty(self):
        assert _INSTALLED_SINK_IDS == []

    def test_remove_on_empty_is_noop(self):
        remove_installed_sinks()  # must not raise
        assert _INSTALLED_SINK_IDS == []


# ---------------------------------------------------------------------------
# Task 8: install_sinks
# ---------------------------------------------------------------------------


class TestInstallSinks:
    def test_installs_single_stderr_sink(self):
        from openagents.observability.config import LoguruSinkConfig

        install_sinks([LoguruSinkConfig(target="stderr")])
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_installs_multiple_sinks(self, tmp_path):
        from openagents.observability.config import LoguruSinkConfig

        install_sinks(
            [
                LoguruSinkConfig(target="stderr", colorize=False),
                LoguruSinkConfig(target=str(tmp_path / "app.log")),
                LoguruSinkConfig(target=str(tmp_path / "events.jsonl"), serialize=True),
            ]
        )
        assert len(_INSTALLED_SINK_IDS) == 3

    def test_stderr_target_routes_to_sys_stderr(self, capsys):
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        install_sinks([LoguruSinkConfig(target="stderr", format="{message}", colorize=False)])
        logger.bind(_openagents=True, _oa_name="test").info("hello-to-stderr")
        captured = capsys.readouterr()
        assert "hello-to-stderr" in captured.err

    def test_stdout_target_routes_to_sys_stdout(self, capsys):
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        install_sinks([LoguruSinkConfig(target="stdout", format="{message}", colorize=False)])
        logger.bind(_openagents=True, _oa_name="test").info("hello-to-stdout")
        captured = capsys.readouterr()
        assert "hello-to-stdout" in captured.out

    def test_file_target_writes_to_path(self, tmp_path):
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        log_path = tmp_path / "out.log"
        install_sinks([LoguruSinkConfig(target=str(log_path), format="{message}")])
        logger.bind(_openagents=True, _oa_name="test").info("written")
        # enqueue defaults to False, so write is sync
        assert log_path.exists()
        assert "written" in log_path.read_text(encoding="utf-8")

    def test_filter_rejects_untagged_records(self, capsys):
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        install_sinks([LoguruSinkConfig(target="stderr", format="{message}", colorize=False)])
        # Untagged: no _openagents=True in extra
        logger.info("untagged-should-not-appear")
        captured = capsys.readouterr()
        assert "untagged-should-not-appear" not in captured.err

    def test_batch_rollback_on_partial_failure(self, tmp_path):
        """Spec test 11: if any sink's add() raises (e.g. invalid rotation),
        all sinks successfully added in this call must be removed."""
        from openagents.observability.config import LoguruSinkConfig

        good = LoguruSinkConfig(target="stderr")
        bad = LoguruSinkConfig(target=str(tmp_path / "x.log"), rotation="not-a-valid-size")
        with pytest.raises(Exception):
            install_sinks([good, bad])
        assert _INSTALLED_SINK_IDS == []

    def test_remove_does_not_touch_user_sinks(self, tmp_path):
        """Spec test 4: user-installed sinks (no _openagents tag) must
        survive our reset."""
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        user_sink_path = tmp_path / "user.log"
        user_sink_id = logger.add(str(user_sink_path), format="{message}")
        try:
            install_sinks([LoguruSinkConfig(target="stderr", colorize=False)])
            assert len(_INSTALLED_SINK_IDS) == 1
            remove_installed_sinks()
            # User's sink still alive
            logger.info("user-still-here")
            assert "user-still-here" in user_sink_path.read_text(encoding="utf-8")
        finally:
            try:
                logger.remove(user_sink_id)
            except ValueError:
                pass

    def test_user_sink_never_receives_openagents_records(self, tmp_path):
        """Spec test 3 (reverse direction): user's own sink — which filters
        OUT our _openagents tag — must not receive records forwarded by
        our intercept handler."""
        from loguru import logger

        from openagents.observability.config import LoguruSinkConfig

        user_sink_path = tmp_path / "user.log"
        user_sink_id = logger.add(
            str(user_sink_path),
            format="{message}",
            filter=lambda r: r["extra"].get("_openagents") is not True,
        )
        try:
            install_sinks([LoguruSinkConfig(target=str(tmp_path / "oa.log"), format="{message}")])
            # Record going through our tagged intercept path
            logger.bind(_openagents=True, _oa_name="test").info("openagents-only")
            # Record from user's own codepath
            logger.info("user-only")
            user_content = user_sink_path.read_text(encoding="utf-8")
            assert "user-only" in user_content
            assert "openagents-only" not in user_content
            oa_content = (tmp_path / "oa.log").read_text(encoding="utf-8")
            assert "openagents-only" in oa_content
            assert "user-only" not in oa_content
        finally:
            try:
                logger.remove(user_sink_id)
            except ValueError:
                pass

    def test_require_loguru_raises_when_loguru_missing(self, monkeypatch):
        """Spec test 7: simulate ImportError by blocking loguru from being
        re-imported. This is tricky because loguru is already imported;
        we shadow the name in sys.modules and block re-import."""
        import importlib
        import sys

        saved = sys.modules.pop("loguru", None)
        real_import = __import__

        def _fake_import(name, *args, **kwargs):
            if name == "loguru":
                raise ImportError(f"mock-block-{name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        try:
            # Force a fresh lookup by reimporting the helper module under the block
            import openagents.observability._loguru as _lg_mod

            importlib.reload(_lg_mod)
            from openagents.observability import LoguruNotInstalledError

            with pytest.raises(LoguruNotInstalledError) as excinfo:
                _lg_mod._require_loguru()
            assert "pip install io-openagent-sdk[loguru]" in str(excinfo.value)
        finally:
            if saved is not None:
                sys.modules["loguru"] = saved
            # Restore the real module after un-monkeypatching
            import openagents.observability._loguru as _lg_mod

            importlib.reload(_lg_mod)


# ---------------------------------------------------------------------------
# Task 9: _LoguruInterceptHandler.emit()
# ---------------------------------------------------------------------------


class TestLoguruInterceptHandler:
    def _build_handler_with_sink(self, tmp_path, **sink_overrides):
        """Helper: install a single file sink and return (handler, logger, path)."""
        from openagents.observability.config import LoguruSinkConfig

        kwargs = {"target": str(tmp_path / "out.log"), "format": "{message}"}
        kwargs.update(sink_overrides)
        install_sinks([LoguruSinkConfig(**kwargs)])
        handler = _LoguruInterceptHandler()
        lg = logging.getLogger("openagents.test")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        return handler, lg, tmp_path / "out.log"

    def test_basic_forward_standard_level(self, tmp_path):
        _, lg, log_path = self._build_handler_with_sink(tmp_path)
        lg.info("hello-forwarded")
        assert "hello-forwarded" in log_path.read_text(encoding="utf-8")

    def test_custom_numeric_level_falls_back_to_levelno(self, tmp_path):
        """Spec test 13."""
        logging.addLevelName(25, "CUSTOMV25")
        _, lg, log_path = self._build_handler_with_sink(
            tmp_path,
            level="DEBUG",
            format="{level.no} {message}",
        )
        lg.log(25, "custom-level-msg")
        content = log_path.read_text(encoding="utf-8")
        assert "custom-level-msg" in content
        # level.no in loguru should reflect the numeric 25 we passed
        assert "25" in content

    def test_exception_forwarded(self, tmp_path):
        """Spec test 10: exception info reaches loguru sink."""
        _, lg, log_path = self._build_handler_with_sink(tmp_path, format="{message}\n{exception}")
        try:
            raise RuntimeError("boom-inner")
        except RuntimeError:
            lg.exception("boom-outer")
        content = log_path.read_text(encoding="utf-8")
        assert "boom-outer" in content
        assert "RuntimeError" in content
        assert "boom-inner" in content

    def test_extras_propagated_to_bind(self, tmp_path):
        """Spec test 15: non-standard LogRecord attrs flow as loguru extra."""
        _, lg, log_path = self._build_handler_with_sink(tmp_path, serialize=True)
        lg.info("with-request-id", extra={"request_id": "r-42"})
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        data = json.loads(line)
        assert data["record"]["extra"]["request_id"] == "r-42"
        assert data["record"]["extra"]["_openagents"] is True

    def test_extras_redacted_before_reaching_sink(self, tmp_path):
        """Spec test 9: RedactFilter runs before forward, so sensitive
        fields on the record are already masked when they reach loguru."""
        from openagents.observability.filters import RedactFilter

        handler, lg, log_path = self._build_handler_with_sink(tmp_path, serialize=True)
        handler.addFilter(RedactFilter(keys=["api_key"], max_value_length=500))
        lg.info("sensitive", extra={"api_key": "sk-abc", "request_id": "r-1"})
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        data = json.loads(line)
        # request_id passes through untouched
        assert data["record"]["extra"]["request_id"] == "r-1"
        # api_key is redacted (the RedactFilter masks it; exact token is
        # implementation-defined — assert it no longer equals "sk-abc")
        assert data["record"]["extra"]["api_key"] != "sk-abc"

    def test_depth_points_to_caller_not_handler(self, tmp_path):
        """Spec test 0: the canonical InterceptHandler pattern walks frames
        so {function} points at the caller function."""
        _, lg, log_path = self._build_handler_with_sink(tmp_path, format="{function}:{line} {message}")

        def _my_caller_func():
            lg.info("from-caller")

        _my_caller_func()
        content = log_path.read_text(encoding="utf-8")
        assert "_my_caller_func" in content
        last = [ln for ln in content.splitlines() if "from-caller" in ln][-1]
        assert last.startswith("_my_caller_func:")


# ---------------------------------------------------------------------------
# Task 10: identity-equality of _LOGRECORD_STD_ATTRS across modules
# ---------------------------------------------------------------------------


def test_logrecord_std_attrs_is_shared_singleton():
    """Spec test 16: prevent silent drift between filters.py and _loguru.py."""
    from openagents.observability import _loguru, filters

    assert _loguru._LOGRECORD_STD_ATTRS is filters._LOGRECORD_STD_ATTRS
