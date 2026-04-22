"""Tests for observability.filters."""

from __future__ import annotations

import logging

from openagents.observability.filters import (
    LevelOverrideFilter,
    PrefixFilter,
    RedactFilter,
)


def _make_record(name: str, level: int = logging.INFO, **extras: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="x.py",
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    return record


class TestPrefixFilter:
    def test_include_whitelist_keeps_match(self) -> None:
        f = PrefixFilter(include=["openagents.llm"], exclude=[])
        assert f.filter(_make_record("openagents.llm.anthropic")) is True

    def test_include_whitelist_drops_non_match(self) -> None:
        f = PrefixFilter(include=["openagents.llm"], exclude=[])
        assert f.filter(_make_record("openagents.events.bus")) is False

    def test_include_none_means_allow_all(self) -> None:
        f = PrefixFilter(include=None, exclude=[])
        assert f.filter(_make_record("openagents.anything")) is True

    def test_exclude_blacklist_drops_match(self) -> None:
        f = PrefixFilter(include=None, exclude=["openagents.events"])
        assert f.filter(_make_record("openagents.events.bus")) is False

    def test_exclude_beats_include_when_both_match(self) -> None:
        f = PrefixFilter(include=["openagents"], exclude=["openagents.events"])
        assert f.filter(_make_record("openagents.events.bus")) is False
        assert f.filter(_make_record("openagents.llm.x")) is True


class TestLevelOverrideFilter:
    def test_promotes_per_logger_level(self) -> None:
        f = LevelOverrideFilter({"openagents.llm": "DEBUG"})
        record = _make_record("openagents.llm.anthropic", level=logging.DEBUG)
        assert f.filter(record) is True

    def test_drops_below_override(self) -> None:
        f = LevelOverrideFilter({"openagents.llm": "WARNING"})
        record = _make_record("openagents.llm.anthropic", level=logging.INFO)
        assert f.filter(record) is False

    def test_passes_through_when_no_override_matches(self) -> None:
        f = LevelOverrideFilter({"openagents.events": "WARNING"})
        record = _make_record("openagents.llm.anthropic", level=logging.INFO)
        assert f.filter(record) is True

    def test_longest_prefix_wins(self) -> None:
        f = LevelOverrideFilter({"openagents": "ERROR", "openagents.llm": "DEBUG"})
        record = _make_record("openagents.llm.anthropic", level=logging.DEBUG)
        assert f.filter(record) is True


class TestRedactFilter:
    def test_masks_matching_key_on_extras(self) -> None:
        f = RedactFilter(keys=["api_key"], max_value_length=1000)
        record = _make_record("openagents.x", api_key="sk-123")
        f.filter(record)
        assert record.api_key == "***"

    def test_truncates_long_string_on_extras(self) -> None:
        f = RedactFilter(keys=[], max_value_length=5)
        record = _make_record("openagents.x", note="a" * 20)
        f.filter(record)
        assert "(truncated 20 chars)" in record.note


class TestLogRecordStdAttrs:
    def test_is_frozenset_at_module_level(self) -> None:
        from openagents.observability.filters import _LOGRECORD_STD_ATTRS

        assert isinstance(_LOGRECORD_STD_ATTRS, frozenset)
        for name in ("msg", "args", "levelname", "levelno", "name", "exc_info"):
            assert name in _LOGRECORD_STD_ATTRS

    def test_covers_canonical_logrecord_attrs(self) -> None:
        """The constant must be a superset of (or equal to) the 22 canonical
        LogRecord attribute names. Guards against accidental shrinkage."""
        from openagents.observability.filters import _LOGRECORD_STD_ATTRS

        canonical = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
        }
        assert canonical <= _LOGRECORD_STD_ATTRS
