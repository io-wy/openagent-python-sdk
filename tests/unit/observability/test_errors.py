"""Tests for observability error classes."""

from __future__ import annotations

import pytest


class TestLoguruNotInstalledError:
    def test_is_importerror_subclass(self):
        from openagents.observability import LoguruNotInstalledError

        assert issubclass(LoguruNotInstalledError, ImportError)

    def test_default_message_contains_pip_hint(self):
        from openagents.observability import LoguruNotInstalledError

        exc = LoguruNotInstalledError()
        assert "loguru" in str(exc)
        assert "pip install io-openagent-sdk[loguru]" in str(exc)

    def test_accepts_custom_message(self):
        from openagents.observability import LoguruNotInstalledError

        exc = LoguruNotInstalledError("custom")
        assert str(exc) == "custom"

    def test_raisable(self):
        from openagents.observability import LoguruNotInstalledError

        with pytest.raises(LoguruNotInstalledError):
            raise LoguruNotInstalledError()
