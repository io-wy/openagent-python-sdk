"""Logging filters for observability configuration."""

from __future__ import annotations

import logging

from openagents.observability.redact import redact

_LEVEL_NAMES = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


# Standard LogRecord attribute names. Extracted to module level so _loguru.py
# can import the exact same frozenset by identity, preventing drift between
# RedactFilter's skip set and the intercept handler's extras harvester.
_LOGRECORD_STD_ATTRS: frozenset[str] = frozenset(
    {
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
)


class PrefixFilter(logging.Filter):
    """Accept or drop records by logger-name prefix. Deny wins over allow."""

    def __init__(self, *, include: list[str] | None, exclude: list[str]) -> None:
        super().__init__()
        self._include = list(include) if include is not None else None
        self._exclude = list(exclude)
        self._openagents_installed = True

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix in self._exclude:
            if record.name == prefix or record.name.startswith(prefix + "."):
                return False
        if self._include is None:
            return True
        for prefix in self._include:
            if record.name == prefix or record.name.startswith(prefix + "."):
                return True
        return False


class LevelOverrideFilter(logging.Filter):
    """Raise per-logger level thresholds without mutating the logger tree.

    Longest-prefix-match wins. A record is dropped only when its level is
    below the override threshold for the most specific matching prefix.
    """

    def __init__(self, per_logger_levels: dict[str, str]) -> None:
        super().__init__()
        self._rules = sorted(
            ((name, _LEVEL_NAMES[level.upper()]) for name, level in per_logger_levels.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self._openagents_installed = True

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix, threshold in self._rules:
            if record.name == prefix or record.name.startswith(prefix + "."):
                return record.levelno >= threshold
        return True


class RedactFilter(logging.Filter):
    """Redact sensitive extras on the record in place before handlers see it."""

    def __init__(self, *, keys: list[str], max_value_length: int) -> None:
        super().__init__()
        self._keys = list(keys)
        self._max = max_value_length
        self._openagents_installed = True

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__.keys()):
            if key.startswith("_") or key in _LOGRECORD_STD_ATTRS:
                continue
            # Wrap the value in a dict so redact() can mask based on key name
            wrapped = {key: record.__dict__[key]}
            redacted = redact(wrapped, keys=self._keys, max_value_length=self._max)
            record.__dict__[key] = redacted[key]
        return True
