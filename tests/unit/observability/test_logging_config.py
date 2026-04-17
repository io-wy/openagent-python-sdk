"""Tests for observability.config."""

from __future__ import annotations

import pytest

from openagents.observability.config import LoggingConfig, load_from_env


class TestDefaults:
    def test_all_defaults(self) -> None:
        c = LoggingConfig()
        assert c.auto_configure is False
        assert c.level == "INFO"
        assert c.per_logger_levels == {}
        assert c.pretty is False
        assert c.stream == "stderr"
        assert c.include_prefixes is None
        assert c.exclude_prefixes == []
        assert "api_key" in c.redact_keys
        assert c.max_value_length == 500
        assert c.show_time is True
        assert c.show_path is False


class TestLevelValidation:
    def test_level_upper_cased(self) -> None:
        assert LoggingConfig(level="debug").level == "DEBUG"

    def test_invalid_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid log level"):
            LoggingConfig(level="VERBOSE")

    def test_per_logger_levels_validated(self) -> None:
        c = LoggingConfig(per_logger_levels={"openagents.llm": "debug"})
        assert c.per_logger_levels == {"openagents.llm": "DEBUG"}

    def test_invalid_per_logger_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid log level"):
            LoggingConfig(per_logger_levels={"openagents.llm": "LOUD"})


class TestEnvParser:
    def test_empty_env_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in [
            "OPENAGENTS_LOG_AUTOCONFIGURE",
            "OPENAGENTS_LOG_LEVEL",
            "OPENAGENTS_LOG_LEVELS",
            "OPENAGENTS_LOG_PRETTY",
            "OPENAGENTS_LOG_STREAM",
            "OPENAGENTS_LOG_INCLUDE",
            "OPENAGENTS_LOG_EXCLUDE",
            "OPENAGENTS_LOG_REDACT",
            "OPENAGENTS_LOG_MAX_VALUE_LENGTH",
        ]:
            monkeypatch.delenv(var, raising=False)
        assert load_from_env() is None

    def test_level_and_pretty_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("OPENAGENTS_LOG_PRETTY", "1")
        config = load_from_env()
        assert config is not None
        assert config.level == "DEBUG"
        assert config.pretty is True

    def test_levels_map_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OPENAGENTS_LOG_LEVELS", "openagents.llm=DEBUG,openagents.events=WARNING"
        )
        config = load_from_env()
        assert config is not None
        assert config.per_logger_levels == {
            "openagents.llm": "DEBUG",
            "openagents.events": "WARNING",
        }

    def test_include_exclude_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAGENTS_LOG_INCLUDE", "openagents.llm,openagents.events")
        monkeypatch.setenv("OPENAGENTS_LOG_EXCLUDE", "openagents.llm.anthropic")
        config = load_from_env()
        assert config is not None
        assert config.include_prefixes == ["openagents.llm", "openagents.events"]
        assert config.exclude_prefixes == ["openagents.llm.anthropic"]

    def test_empty_string_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "")
        assert load_from_env() is None


class TestEnvOverridesConfig:
    def test_merge_applies_only_set_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openagents.observability.config import merge_env_overrides

        base = LoggingConfig(level="INFO", pretty=False)
        monkeypatch.setenv("OPENAGENTS_LOG_LEVEL", "DEBUG")
        monkeypatch.delenv("OPENAGENTS_LOG_PRETTY", raising=False)
        merged = merge_env_overrides(base)
        assert merged.level == "DEBUG"
        assert merged.pretty is False  # unset env var did not override
