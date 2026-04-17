# Debug Logging + Rich Pretty Output — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified `openagents.observability` subpackage that pretty-prints stdlib logs (via Rich, opt-in), and a symmetric `RichConsoleEventBus` event-stream sink — both configurable, both filterable, both non-breaking.

**Architecture:** New top-level `openagents/observability/` subpackage owns `configure()`/`LoggingConfig`/filters/redaction/rich helpers. A new `RichConsoleEventBus` plugin in `plugins/builtin/events/` reuses that subpackage. `rich` ships as an optional `[rich]` pyproject extra (included in `[dev]`). `Runtime.__init__` gains a small opt-in hook that calls `configure()` only when `config.logging.auto_configure` is truthy, preserving library etiquette.

**Tech Stack:** Python ≥ 3.10, `pydantic` v2, `logging` stdlib, `rich>=13.7.0` (optional), `pytest` + `pytest-asyncio`, `uv` for dependency management.

**Spec:** `docs/superpowers/specs/2026-04-17-debug-logging-rich-design.md`

---

## File Inventory

Files this plan creates:

- `openagents/observability/__init__.py`
- `openagents/observability/errors.py`
- `openagents/observability/redact.py`
- `openagents/observability/_rich.py`
- `openagents/observability/filters.py`
- `openagents/observability/config.py`
- `openagents/observability/logging.py`
- `openagents/plugins/builtin/events/rich_console.py`
- `tests/unit/observability/__init__.py`
- `tests/unit/observability/test_redact.py`
- `tests/unit/observability/test_filters.py`
- `tests/unit/observability/test_logging_config.py`
- `tests/unit/observability/test_configure.py`
- `tests/unit/observability/test_rich_console_bus.py`
- `tests/unit/observability/test_file_logging_extended.py`
- `tests/integration/test_runtime_auto_configure.py`
- `tests/integration/test_env_override.py`

Files this plan modifies:

- `pyproject.toml` — add `[rich]` extra; `[dev]` depends on it; `[all]` includes `rich`
- `openagents/plugins/builtin/events/file_logging.py` — add `redact_keys`, `max_value_length`, `exclude_events`; upgrade `include_events`/`exclude_events` to fnmatch globs
- `openagents/plugins/builtin/events/__init__.py` — export `RichConsoleEventBus`
- `openagents/plugins/registry.py` — register `events.rich_console`
- `openagents/config/schema.py` — add `LoggingConfig` import and `logging: LoggingConfig | None = None` to `AppConfig`
- `openagents/runtime/runtime.py` — auto-configure hook in `Runtime.__init__`
- `docs/developer-guide.md` — new "调试与可观测性" section
- `docs/configuration.md` — `logging` section field table
- `docs/seams-and-extension-points.md` — add `rich_console` alongside `file_logging`/`otel_bridge`
- `examples/quickstart/agent.json` — add `logging` section

---

## Task 1: Add `[rich]` extra to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml**

Add `rich` extra and wire `[dev]`/`[all]` to include it.

```toml
[project.optional-dependencies]
dev = [
    "coverage[toml]>=7.6.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "io-openagent-sdk[rich]",
]
mcp = [
    "mcp>=1.0.0",
]
mem0 = [
    "mem0ai>=1.0.5",
]
openai = [
    "httpx>=0.27.0",
    "openai>=1.0.0",
]
otel = [
    "opentelemetry-api>=1.25.0",
]
rich = [
    "rich>=13.7.0",
]
sqlite = [
    "aiosqlite>=0.20.0",
]
tokenizers = [
    "tiktoken>=0.7.0",
]
yaml = [
    "pyyaml>=6.0",
]
all = [
    "io-openagent-sdk[mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml]",
]
```

- [ ] **Step 2: Sync and verify rich is importable**

Run: `uv sync --extra dev`
Expected: install completes, `uv run python -c "import rich; print(rich.__version__)"` prints a version ≥ `13.7.0`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(pyproject): add [rich] extra for optional pretty logging"
```

---

## Task 2: `openagents/observability/errors.py` — RichNotInstalledError

**Files:**
- Create: `openagents/observability/errors.py`

- [ ] **Step 1: Create the file**

```python
"""Observability errors."""

from __future__ import annotations


class RichNotInstalledError(ImportError):
    """Raised when rich-powered pretty output is requested but rich is missing.

    Mirrors the posture of mcp_tool / mem0_memory: if the user explicitly
    asks for the feature and the optional extra isn't installed, fail loud
    and give them the exact pip command.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "rich is required for pretty output. Install with: pip install io-openagent-sdk[rich]"
        )
```

- [ ] **Step 2: Commit**

```bash
git add openagents/observability/errors.py
git commit -m "feat(observability): scaffold errors.RichNotInstalledError"
```

---

## Task 3: `openagents/observability/redact.py` — pure redactor

**Files:**
- Create: `openagents/observability/redact.py`
- Test: `tests/unit/observability/__init__.py`
- Test: `tests/unit/observability/test_redact.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/__init__.py` (empty file to make it a package):

```python
```

Create `tests/unit/observability/test_redact.py`:

```python
"""Tests for observability.redact."""

from __future__ import annotations

import pytest

from openagents.observability.redact import redact


class TestRedactKeys:
    def test_masks_matching_key_case_insensitive(self) -> None:
        out = redact({"API_KEY": "sk-123"}, keys=["api_key"], max_value_length=1000)
        assert out == {"API_KEY": "***"}

    def test_leaves_unknown_keys_alone(self) -> None:
        out = redact({"foo": "bar"}, keys=["api_key"], max_value_length=1000)
        assert out == {"foo": "bar"}

    def test_recurses_into_nested_dicts(self) -> None:
        payload = {"outer": {"token": "abc", "safe": "keep"}}
        out = redact(payload, keys=["token"], max_value_length=1000)
        assert out == {"outer": {"token": "***", "safe": "keep"}}

    def test_recurses_into_lists(self) -> None:
        payload = {"items": [{"password": "p1"}, {"password": "p2"}]}
        out = redact(payload, keys=["password"], max_value_length=1000)
        assert out == {"items": [{"password": "***"}, {"password": "***"}]}


class TestTruncation:
    def test_truncates_long_strings(self) -> None:
        long = "x" * 1000
        out = redact({"note": long}, keys=[], max_value_length=10)
        assert out["note"].startswith("xxxxxxxxxx")
        assert "(truncated" in out["note"]
        assert out["note"].endswith("chars)")

    def test_short_strings_untouched(self) -> None:
        out = redact({"note": "short"}, keys=[], max_value_length=100)
        assert out == {"note": "short"}

    def test_truncation_applied_before_would_exceed(self) -> None:
        out = redact({"note": "x" * 11}, keys=[], max_value_length=10)
        assert "(truncated 11 chars)" in out["note"]


class TestScalars:
    def test_passes_through_int_float_bool_none(self) -> None:
        payload = {"a": 1, "b": 1.5, "c": True, "d": None}
        out = redact(payload, keys=[], max_value_length=1000)
        assert out == payload


class TestImmutability:
    def test_does_not_mutate_input(self) -> None:
        original = {"api_key": "sk-123", "nested": {"token": "abc"}}
        snapshot = {"api_key": "sk-123", "nested": {"token": "abc"}}
        _ = redact(original, keys=["api_key", "token"], max_value_length=1000)
        assert original == snapshot


class TestCircularGuard:
    def test_cycle_replaced_with_marker(self) -> None:
        a: dict = {"name": "a"}
        a["self"] = a
        out = redact(a, keys=[], max_value_length=1000)
        assert out["name"] == "a"
        assert out["self"] == "<circular>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_redact.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openagents.observability'` (or similar).

- [ ] **Step 3: Create the subpackage scaffold**

Create `openagents/observability/__init__.py` (empty for now):

```python
"""Observability primitives: logging configuration, redaction, rich helpers."""
```

- [ ] **Step 4: Implement redact.py**

Create `openagents/observability/redact.py`:

```python
"""Pure-function redactor used by both stdlib logging and event bus sinks."""

from __future__ import annotations

from typing import Any

_TRUNCATED_SUFFIX = " (truncated {n} chars)"


def redact(value: Any, *, keys: list[str], max_value_length: int) -> Any:
    """Return a deep-copied version of value with sensitive keys masked and long strings truncated.

    Rules (applied in order):
    1. Case-insensitive key-name match against keys -> value becomes "***".
    2. String values exceeding max_value_length -> truncated with suffix.
    3. Nested dict/list recursion; circular references replaced with "<circular>".

    Scalars (int/float/bool/None) pass through unchanged.
    """
    lowered = {k.lower() for k in keys}
    return _walk(value, lowered, max_value_length, set())


def _walk(node: Any, keys_lower: set[str], max_len: int, seen: set[int]) -> Any:
    if isinstance(node, dict):
        node_id = id(node)
        if node_id in seen:
            return "<circular>"
        seen = seen | {node_id}
        return {
            key: (
                "***"
                if isinstance(key, str) and key.lower() in keys_lower
                else _walk(val, keys_lower, max_len, seen)
            )
            for key, val in node.items()
        }
    if isinstance(node, list):
        node_id = id(node)
        if node_id in seen:
            return "<circular>"
        seen = seen | {node_id}
        return [_walk(item, keys_lower, max_len, seen) for item in node]
    if isinstance(node, str) and len(node) > max_len:
        return node[:max_len] + _TRUNCATED_SUFFIX.format(n=len(node))
    return node
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_redact.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/observability/__init__.py openagents/observability/redact.py tests/unit/observability/__init__.py tests/unit/observability/test_redact.py
git commit -m "feat(observability): pure redactor with key-mask, truncation, cycle guard"
```

---

## Task 4: `openagents/observability/_rich.py` — rich console & handler factory

**Files:**
- Create: `openagents/observability/_rich.py`

This module is tested indirectly by `test_configure.py` and `test_rich_console_bus.py` later. It has no standalone test of its own because the rich-rendering output is not something we unit-test line-by-line (that would be brittle).

- [ ] **Step 1: Create _rich.py**

```python
"""Internal rich helpers.

All `rich` imports live here behind import-time guards. Public callers
(configure(), RichConsoleEventBus) use the factories exposed here and
receive RichNotInstalledError if rich is missing.
"""

from __future__ import annotations

from typing import Any, Literal

from openagents.observability.errors import RichNotInstalledError


def _require_rich() -> Any:
    try:
        import rich  # noqa: F401
    except ImportError as exc:
        raise RichNotInstalledError() from exc
    return rich


def make_console(stream: Literal["stdout", "stderr"] = "stderr") -> Any:
    """Return a rich.console.Console writing to the requested stream."""
    _require_rich()
    import sys

    from rich.console import Console

    target = sys.stderr if stream == "stderr" else sys.stdout
    return Console(file=target, force_terminal=None, highlight=False)


def make_rich_handler(*, stream: Literal["stdout", "stderr"], show_time: bool, show_path: bool) -> Any:
    """Return a configured rich.logging.RichHandler."""
    _require_rich()
    from rich.logging import RichHandler

    console = make_console(stream)
    handler = RichHandler(
        console=console,
        show_time=show_time,
        show_level=True,
        show_path=show_path,
        rich_tracebacks=True,
        markup=False,
    )
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler


def render_event_row(event: Any, *, show_payload: bool) -> Any:
    """Render a RuntimeEvent into a rich Renderable.

    - show_payload=False: single-line Text "ts  name  key=val ..."
    - show_payload=True: Panel with per-field rows
    """
    _require_rich()
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    name = event.name
    payload = event.payload or {}

    if not show_payload:
        line = Text()
        line.append(f"{name}  ", style="bold")
        for i, (k, v) in enumerate(payload.items()):
            if i > 0:
                line.append(" ")
            line.append(f"{k}=")
            line.append(repr(v))
        return line

    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="bold")
    table.add_column()
    for k, v in payload.items():
        table.add_row(f"{k} =", repr(v))
    return Panel(table, title=name, title_align="left")
```

- [ ] **Step 2: Smoke-check the imports don't error**

Run: `uv run python -c "from openagents.observability._rich import make_console, make_rich_handler, render_event_row; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add openagents/observability/_rich.py
git commit -m "feat(observability): rich Console/Handler factories behind import guard"
```

---

## Task 5: `openagents/observability/filters.py` — logging filters

**Files:**
- Create: `openagents/observability/filters.py`
- Test: `tests/unit/observability/test_filters.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/test_filters.py`:

```python
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
        f = LevelOverrideFilter(
            {"openagents": "ERROR", "openagents.llm": "DEBUG"}
        )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_filters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openagents.observability.filters'`.

- [ ] **Step 3: Implement filters.py**

Create `openagents/observability/filters.py`:

```python
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
            (
                (name, _LEVEL_NAMES[level.upper()])
                for name, level in per_logger_levels.items()
            ),
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
        skip = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message", "module",
            "msecs", "msg", "name", "pathname", "process", "processName",
            "relativeCreated", "stack_info", "thread", "threadName",
        }
        for key in list(record.__dict__.keys()):
            if key.startswith("_") or key in skip:
                continue
            record.__dict__[key] = redact(
                record.__dict__[key], keys=self._keys, max_value_length=self._max
            )
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_filters.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add openagents/observability/filters.py tests/unit/observability/test_filters.py
git commit -m "feat(observability): prefix/level-override/redact logging filters"
```

---

## Task 6: `openagents/observability/config.py` — LoggingConfig + env parser

**Files:**
- Create: `openagents/observability/config.py`
- Test: `tests/unit/observability/test_logging_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/test_logging_config.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_logging_config.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement config.py**

Create `openagents/observability/config.py`:

```python
"""LoggingConfig pydantic model and env-var parser."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_VALID_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def _normalize_level(value: str) -> str:
    up = value.upper()
    if up not in _VALID_LEVELS:
        raise ValueError(f"invalid log level: {value!r}")
    return up


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_configure: bool = False
    level: str = "INFO"
    per_logger_levels: dict[str, str] = Field(default_factory=dict)
    pretty: bool = False
    stream: Literal["stdout", "stderr"] = "stderr"
    include_prefixes: list[str] | None = None
    exclude_prefixes: list[str] = Field(default_factory=list)
    redact_keys: list[str] = Field(
        default_factory=lambda: [
            "api_key",
            "authorization",
            "token",
            "secret",
            "password",
        ]
    )
    max_value_length: int = 500
    show_time: bool = True
    show_path: bool = False

    @field_validator("level", mode="before")
    @classmethod
    def _v_level(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("level must be a string")
        return _normalize_level(value)

    @field_validator("per_logger_levels", mode="before")
    @classmethod
    def _v_per_logger(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("per_logger_levels must be a mapping")
        return {str(k): _normalize_level(str(v)) for k, v in value.items()}


_FIELD_ENV_MAP = {
    "auto_configure": "OPENAGENTS_LOG_AUTOCONFIGURE",
    "level": "OPENAGENTS_LOG_LEVEL",
    "per_logger_levels": "OPENAGENTS_LOG_LEVELS",
    "pretty": "OPENAGENTS_LOG_PRETTY",
    "stream": "OPENAGENTS_LOG_STREAM",
    "include_prefixes": "OPENAGENTS_LOG_INCLUDE",
    "exclude_prefixes": "OPENAGENTS_LOG_EXCLUDE",
    "redact_keys": "OPENAGENTS_LOG_REDACT",
    "max_value_length": "OPENAGENTS_LOG_MAX_VALUE_LENGTH",
}


def _env_value(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _env_overrides() -> dict[str, Any]:
    """Return only those fields whose env vars are set (non-empty)."""
    overrides: dict[str, Any] = {}
    for field, env_name in _FIELD_ENV_MAP.items():
        raw = _env_value(env_name)
        if raw is None:
            continue
        if field in {"auto_configure", "pretty"}:
            overrides[field] = raw.lower() in {"1", "true", "yes", "on"}
        elif field == "per_logger_levels":
            pairs: dict[str, str] = {}
            for part in raw.split(","):
                if "=" not in part:
                    continue
                k, _, v = part.partition("=")
                pairs[k.strip()] = v.strip()
            overrides[field] = pairs
        elif field in {"include_prefixes", "exclude_prefixes", "redact_keys"}:
            overrides[field] = [p.strip() for p in raw.split(",") if p.strip()]
        elif field == "max_value_length":
            overrides[field] = int(raw)
        else:
            overrides[field] = raw
    return overrides


def load_from_env() -> LoggingConfig | None:
    """Return a LoggingConfig built entirely from env vars, or None if none set."""
    overrides = _env_overrides()
    if not overrides:
        return None
    return LoggingConfig(**overrides)


def merge_env_overrides(base: LoggingConfig) -> LoggingConfig:
    """Return a copy of base with env-var overrides applied (unset vars don't override)."""
    overrides = _env_overrides()
    if not overrides:
        return base
    merged = base.model_dump()
    merged.update(overrides)
    return LoggingConfig(**merged)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_logging_config.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add openagents/observability/config.py tests/unit/observability/test_logging_config.py
git commit -m "feat(observability): LoggingConfig pydantic model + env var parser"
```

---

## Task 7: `openagents/observability/logging.py` — configure / reset

**Files:**
- Create: `openagents/observability/logging.py`
- Test: `tests/unit/observability/test_configure.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/test_configure.py`:

```python
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
        caplog.set_level(logging.WARNING, logger="openagents.observability.logging")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_configure.py -v`
Expected: FAIL with ImportError from `openagents.observability` (no `configure` yet).

- [ ] **Step 3: Implement logging.py**

Create `openagents/observability/logging.py`:

```python
"""Public logging configuration for the 'openagents.*' namespace."""

from __future__ import annotations

import logging
from typing import Any

from openagents.observability.config import (
    LoggingConfig,
    load_from_env,
    merge_env_overrides,
)
from openagents.observability.filters import (
    LevelOverrideFilter,
    PrefixFilter,
    RedactFilter,
)

_LOGGER_ROOT = "openagents"
_OBS_LOGGER = logging.getLogger("openagents.observability.logging")


def configure(config: LoggingConfig | None = None) -> None:
    """Install handlers/filters on the 'openagents' logger tree.

    Idempotent. Safe to call from Runtime.reload(). Never touches the
    root logger or any logger outside the 'openagents.*' namespace.

    Raises RichNotInstalledError when config.pretty=True but rich is missing.
    """
    if config is None:
        config = load_from_env() or LoggingConfig()
    config = merge_env_overrides(config)

    _warn_on_foreign_loggers(config)
    reset_logging()

    root = logging.getLogger(_LOGGER_ROOT)
    root.setLevel(_name_to_level(config.level))
    # Prevent double-emission when an application also configures root handlers.
    root.propagate = False

    handler = _build_handler(config)
    handler.addFilter(
        PrefixFilter(include=config.include_prefixes, exclude=config.exclude_prefixes)
    )
    if config.per_logger_levels:
        handler.addFilter(LevelOverrideFilter(config.per_logger_levels))
    if config.redact_keys:
        handler.addFilter(
            RedactFilter(keys=config.redact_keys, max_value_length=config.max_value_length)
        )
    root.addHandler(handler)


def configure_from_env() -> None:
    """Build a LoggingConfig from OPENAGENTS_LOG_* env vars, then configure()."""
    cfg = load_from_env() or LoggingConfig()
    configure(cfg)


def reset_logging() -> None:
    """Remove all handlers tagged _openagents_installed=True from the openagents logger."""
    root = logging.getLogger(_LOGGER_ROOT)
    to_remove = [h for h in root.handlers if getattr(h, "_openagents_installed", False)]
    for handler in to_remove:
        root.removeHandler(handler)


def _warn_on_foreign_loggers(config: LoggingConfig) -> None:
    foreign = [
        name
        for name in config.per_logger_levels
        if name != _LOGGER_ROOT and not name.startswith(_LOGGER_ROOT + ".")
    ]
    for name in foreign:
        _OBS_LOGGER.warning(
            "logger '%s' outside 'openagents.*' namespace is ignored (library etiquette)",
            name,
        )


def _build_handler(config: LoggingConfig) -> logging.Handler:
    if config.pretty:
        from openagents.observability._rich import make_rich_handler

        return make_rich_handler(
            stream=config.stream,
            show_time=config.show_time,
            show_path=config.show_path,
        )
    import sys

    stream = sys.stderr if config.stream == "stderr" else sys.stdout
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s")
    )
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler


def _name_to_level(name: str) -> int:
    return getattr(logging, name.upper(), logging.INFO)
```

- [ ] **Step 4: Expose public API from `observability/__init__.py`**

Replace `openagents/observability/__init__.py` contents:

```python
"""Observability primitives: logging configuration, redaction, rich helpers."""

from openagents.observability.config import LoggingConfig
from openagents.observability.errors import RichNotInstalledError
from openagents.observability.logging import (
    configure,
    configure_from_env,
    reset_logging,
)

__all__ = [
    "LoggingConfig",
    "RichNotInstalledError",
    "configure",
    "configure_from_env",
    "reset_logging",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/observability/test_configure.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 6: Run full observability subtree to make sure nothing regressed**

Run: `uv run pytest tests/unit/observability -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add openagents/observability/logging.py openagents/observability/__init__.py tests/unit/observability/test_configure.py
git commit -m "feat(observability): configure()/reset_logging() with library-etiquette guard"
```

---

## Task 8: Extend `FileLoggingEventBus` with redact/glob/exclude_events

**Files:**
- Modify: `openagents/plugins/builtin/events/file_logging.py`
- Test: `tests/unit/observability/test_file_logging_extended.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/test_file_logging_extended.py`:

```python
"""Tests for FileLoggingEventBus extended fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus


@pytest.mark.asyncio
async def test_redact_keys_applied(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log),
            "redact_keys": ["api_key"],
            "inner": {"type": "async"},
        }
    )
    await bus.emit("llm.called", agent_id="a1", api_key="sk-123")
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["payload"]["api_key"] == "***"
    assert line["payload"]["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_max_value_length_truncates(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log),
            "max_value_length": 10,
            "inner": {"type": "async"},
        }
    )
    await bus.emit("llm.chunk", delta="x" * 100)
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert "truncated 100 chars" in line["payload"]["delta"]


@pytest.mark.asyncio
async def test_exclude_events_drops_matches(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log),
            "exclude_events": ["llm.chunk"],
            "inner": {"type": "async"},
        }
    )
    await bus.emit("llm.chunk", delta="x")
    await bus.emit("llm.succeeded", tokens=10)
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["name"] == "llm.succeeded"


@pytest.mark.asyncio
async def test_include_events_glob(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log),
            "include_events": ["tool.*"],
            "inner": {"type": "async"},
        }
    )
    await bus.emit("tool.called", agent_id="a1")
    await bus.emit("llm.called", agent_id="a1")
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["name"] == "tool.called"


@pytest.mark.asyncio
async def test_exclude_wins_over_include(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log),
            "include_events": ["tool.*"],
            "exclude_events": ["tool.failed"],
            "inner": {"type": "async"},
        }
    )
    await bus.emit("tool.called", agent_id="a1")
    await bus.emit("tool.failed", agent_id="a1")
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["name"] == "tool.called"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_file_logging_extended.py -v`
Expected: FAIL — `Config` does not accept `redact_keys`, `max_value_length`, or `exclude_events`.

- [ ] **Step 3: Modify `openagents/plugins/builtin/events/file_logging.py`**

Replace the file contents:

```python
"""File-logging event bus wrapper."""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.observability.redact import redact

logger = logging.getLogger("openagents.events.file_logging")


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


class FileLoggingEventBus(EventBusPlugin):
    """Wraps another event bus and appends every matched event to an NDJSON log.

    What:
        Forwards every emit to an inner bus first (so subscribers always
        run), then appends a JSON line to ``log_path``. Supports fnmatch
        glob filtering via ``include_events``/``exclude_events``, payload
        redaction via ``redact_keys``, and long-value truncation via
        ``max_value_length``. File-write failures are logged and swallowed -
        event delivery is never disrupted by IO errors.

    Usage:
        ``{"events": {"type": "file_logging", "config": {"log_path":
        ".logs/events.ndjson", "inner": {"type": "async"},
        "include_events": ["tool.*"], "redact_keys": ["api_key"]}}}``

    Depends on:
        - the local filesystem at ``log_path``
        - an inner event bus loaded via
          :func:`openagents.plugins.loader.load_plugin`
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        log_path: str
        include_events: list[str] | None = None
        exclude_events: list[str] = Field(default_factory=list)
        redact_keys: list[str] = Field(default_factory=list)
        max_value_length: int = 10_000
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._log_path = Path(cfg.log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._include = list(cfg.include_events) if cfg.include_events is not None else None
        self._exclude = list(cfg.exclude_events)
        self._redact_keys = list(cfg.redact_keys)
        self._max_value_length = cfg.max_value_length
        inner_ref = dict(cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def _should_log(self, event_name: str) -> bool:
        if self._exclude and _matches_any(event_name, self._exclude):
            return False
        if self._include is None:
            return True
        return _matches_any(event_name, self._include)

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._should_log(event_name):
            try:
                rendered_payload = (
                    redact(payload, keys=self._redact_keys, max_value_length=self._max_value_length)
                    if self._redact_keys or self._max_value_length
                    else payload
                )
                line = json.dumps(
                    {
                        "name": event_name,
                        "payload": rendered_payload,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                with open(self._log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as exc:
                logger.error("file_logging: append failed: %s", exc)
        return event

    async def get_history(self, event_name: str | None = None, limit: int | None = None) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
```

- [ ] **Step 4: Run new tests and full file_logging test suite**

Run: `uv run pytest tests/unit/observability/test_file_logging_extended.py tests/unit -k file_logging -v`
Expected: all PASS (new ones + existing `test_file_logging` stay green because glob is a superset of exact match).

- [ ] **Step 5: Commit**

```bash
git add openagents/plugins/builtin/events/file_logging.py tests/unit/observability/test_file_logging_extended.py
git commit -m "feat(events/file_logging): add redact_keys, max_value_length, exclude_events, glob filtering"
```

---

## Task 9: New `RichConsoleEventBus` plugin

**Files:**
- Create: `openagents/plugins/builtin/events/rich_console.py`
- Modify: `openagents/plugins/builtin/events/__init__.py`
- Test: `tests/unit/observability/test_rich_console_bus.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/observability/test_rich_console_bus.py`:

```python
"""Tests for RichConsoleEventBus."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("rich")

from openagents.plugins.builtin.events.rich_console import RichConsoleEventBus


class _CaptureConsole:
    def __init__(self) -> None:
        self.rendered: list[Any] = []

    def print(self, obj: Any) -> None:
        self.rendered.append(obj)


def _make_bus(**overrides: Any) -> tuple[RichConsoleEventBus, _CaptureConsole]:
    base = {"inner": {"type": "async"}, "show_payload": True}
    base.update(overrides)
    bus = RichConsoleEventBus(config=base)
    console = _CaptureConsole()
    bus._console = console  # type: ignore[attr-defined]
    return bus, console


@pytest.mark.asyncio
async def test_emits_and_renders() -> None:
    bus, console = _make_bus()
    await bus.emit("tool.called", agent_id="a1", tool="bash")
    assert len(console.rendered) == 1


@pytest.mark.asyncio
async def test_include_events_glob_filters() -> None:
    bus, console = _make_bus(include_events=["tool.*"])
    await bus.emit("tool.called", x=1)
    await bus.emit("llm.called", x=1)
    assert len(console.rendered) == 1


@pytest.mark.asyncio
async def test_exclude_events_wins() -> None:
    bus, console = _make_bus(include_events=["tool.*"], exclude_events=["tool.failed"])
    await bus.emit("tool.called", x=1)
    await bus.emit("tool.failed", x=1)
    assert len(console.rendered) == 1


@pytest.mark.asyncio
async def test_redact_keys_applied() -> None:
    bus, console = _make_bus(redact_keys=["api_key"])
    await bus.emit("llm.called", api_key="sk-123", agent_id="a1")
    # The rendered object carries Panel with masked content; inspect via string form
    from rich.console import Console as RichConsole
    from io import StringIO

    buf = StringIO()
    real = RichConsole(file=buf, force_terminal=False, highlight=False)
    real.print(console.rendered[0])
    rendered = buf.getvalue()
    assert "***" in rendered
    assert "sk-123" not in rendered


@pytest.mark.asyncio
async def test_render_failure_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenConsole:
        def print(self, obj: Any) -> None:
            raise RuntimeError("boom")

    bus, _ = _make_bus()
    bus._console = BrokenConsole()  # type: ignore[attr-defined]
    # Inner bus still sees the emit; exception is logged, not raised
    event = await bus.emit("tool.called", x=1)
    assert event.name == "tool.called"
    assert any("rich_console render failed" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_inner_history_delegated() -> None:
    bus, _ = _make_bus()
    await bus.emit("tool.called", x=1)
    history = await bus.get_history()
    assert len(history) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/observability/test_rich_console_bus.py -v`
Expected: FAIL — `RichConsoleEventBus` does not exist.

- [ ] **Step 3: Implement rich_console.py**

Create `openagents/plugins/builtin/events/rich_console.py`:

```python
"""Rich-powered console event bus wrapper."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.observability._rich import make_console, render_event_row
from openagents.observability.redact import redact

logger = logging.getLogger("openagents.events.rich_console")


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


class RichConsoleEventBus(EventBusPlugin):
    """Wraps another event bus and pretty-prints every matched event to the console.

    What:
        Forwards every emit to an inner bus first (so subscribers always
        run), then renders a rich Text/Panel to stdout/stderr. Filters by
        fnmatch globs (``include_events``/``exclude_events``); deny wins.
        Payload redaction via ``redact_keys`` and long-value truncation via
        ``max_value_length``. Render failures are logged and swallowed -
        event delivery is never disrupted.

    Usage:
        ``{"events": {"type": "rich_console", "config": {"inner":
        {"type": "async"}, "show_payload": true}}}``

    Depends on:
        - ``rich>=13.7.0`` (``pip install io-openagent-sdk[rich]``)
        - an inner event bus loaded via
          :func:`openagents.plugins.loader.load_plugin`
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        include_events: list[str] | None = None
        exclude_events: list[str] = Field(default_factory=list)
        redact_keys: list[str] = Field(
            default_factory=lambda: [
                "api_key",
                "authorization",
                "token",
                "secret",
                "password",
            ]
        )
        max_value_length: int = 500
        show_payload: bool = True
        stream: Literal["stdout", "stderr"] = "stderr"
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._include = list(cfg.include_events) if cfg.include_events is not None else None
        self._exclude = list(cfg.exclude_events)
        self._redact_keys = list(cfg.redact_keys)
        self._max_value_length = cfg.max_value_length
        self._show_payload = cfg.show_payload
        self._console = make_console(cfg.stream)
        inner_ref = dict(cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def _should_render(self, event_name: str) -> bool:
        if self._exclude and _matches_any(event_name, self._exclude):
            return False
        if self._include is None:
            return True
        return _matches_any(event_name, self._include)

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._should_render(event_name):
            try:
                redacted_payload = redact(
                    payload, keys=self._redact_keys, max_value_length=self._max_value_length
                )
                rendered_event = RuntimeEvent(name=event.name, payload=redacted_payload)
                renderable = render_event_row(rendered_event, show_payload=self._show_payload)
                self._console.print(renderable)
            except Exception as exc:
                logger.error("rich_console render failed: %s", exc, exc_info=True)
        return event

    async def get_history(self, event_name: str | None = None, limit: int | None = None) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
```

- [ ] **Step 4: Export from events `__init__.py`**

Replace `openagents/plugins/builtin/events/__init__.py`:

```python
"""Builtin event bus plugins."""

from .async_event_bus import AsyncEventBus
from .file_logging import FileLoggingEventBus
from .rich_console import RichConsoleEventBus

__all__ = ["AsyncEventBus", "FileLoggingEventBus", "RichConsoleEventBus"]
```

- [ ] **Step 5: Run new tests**

Run: `uv run pytest tests/unit/observability/test_rich_console_bus.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/plugins/builtin/events/rich_console.py openagents/plugins/builtin/events/__init__.py tests/unit/observability/test_rich_console_bus.py
git commit -m "feat(events): add RichConsoleEventBus wrapper with glob filters and redaction"
```

---

## Task 10: Register `events.rich_console` in plugin registry

**Files:**
- Modify: `openagents/plugins/registry.py`

- [ ] **Step 1: Edit registry.py**

Add import:

```python
from openagents.plugins.builtin.events.rich_console import RichConsoleEventBus
```

Add to the `events` dict in `_BUILTIN_REGISTRY`:

```python
    "events": {
        "async": AsyncEventBus,
        "file_logging": FileLoggingEventBus,
        "otel_bridge": OtelEventBusBridge,
        "rich_console": RichConsoleEventBus,
    },
```

- [ ] **Step 2: Add a registry assertion test**

Create or append to `tests/unit/observability/test_rich_console_bus.py` (append at the bottom):

```python


def test_registered_in_builtin_registry() -> None:
    from openagents.plugins.registry import get_builtin_plugin_class

    cls = get_builtin_plugin_class("events", "rich_console")
    assert cls is RichConsoleEventBus
```

- [ ] **Step 3: Run full observability unit tests**

Run: `uv run pytest tests/unit/observability -v`
Expected: all PASS, including the new registry test.

- [ ] **Step 4: Also run CLI list-plugins test if present**

Run: `uv run pytest tests/unit -k "cli" -v`
Expected: existing `list-plugins` tests still pass (the new entry is additive).

- [ ] **Step 5: Commit**

```bash
git add openagents/plugins/registry.py tests/unit/observability/test_rich_console_bus.py
git commit -m "feat(registry): register events.rich_console"
```

---

## Task 11: Add `logging` section to `AppConfig` schema

**Files:**
- Modify: `openagents/config/schema.py`

- [ ] **Step 1: Edit `schema.py`**

Near the top add import:

```python
from openagents.observability.config import LoggingConfig
```

Add a field to `AppConfig`:

```python
class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    agents: list[AgentDefinition] = Field(default_factory=list)
    runtime: RuntimeRef = Field(default_factory=lambda: RuntimeRef(type="default"))
    session: SessionRef = Field(default_factory=lambda: SessionRef(type="in_memory"))
    events: EventBusRef = Field(default_factory=lambda: EventBusRef(type="async"))
    skills: SkillsRef = Field(default_factory=lambda: SkillsRef(type="local"))
    logging: LoggingConfig | None = None
    # ... (rest unchanged)
```

- [ ] **Step 2: Add a schema test**

Create `tests/unit/observability/test_app_config_logging.py`:

```python
"""Tests for AppConfig.logging field."""

from __future__ import annotations

from openagents.config.loader import load_config_dict
from openagents.observability.config import LoggingConfig


def _base_config(extras: dict | None = None) -> dict:
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
    if extras:
        base.update(extras)
    return base


def test_logging_defaults_to_none() -> None:
    cfg = load_config_dict(_base_config())
    assert cfg.logging is None


def test_logging_parsed_from_dict() -> None:
    cfg = load_config_dict(
        _base_config(
            {"logging": {"auto_configure": True, "level": "DEBUG", "pretty": True}}
        )
    )
    assert isinstance(cfg.logging, LoggingConfig)
    assert cfg.logging.auto_configure is True
    assert cfg.logging.level == "DEBUG"
    assert cfg.logging.pretty is True


def test_invalid_level_rejected() -> None:
    import pytest

    with pytest.raises(Exception):
        load_config_dict(_base_config({"logging": {"level": "LOUD"}}))
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/observability/test_app_config_logging.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 4: Run the full config test suite for regressions**

Run: `uv run pytest tests/unit -k config -v`
Expected: all PASS (logging is optional with default None).

- [ ] **Step 5: Commit**

```bash
git add openagents/config/schema.py tests/unit/observability/test_app_config_logging.py
git commit -m "feat(config): add optional AppConfig.logging field"
```

---

## Task 12: Auto-configure hook in `Runtime.__init__`

**Files:**
- Modify: `openagents/runtime/runtime.py`
- Test: `tests/integration/test_runtime_auto_configure.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_runtime_auto_configure.py`:

```python
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
    with patch(
        "openagents.observability.logging.configure", autospec=True
    ) as mock_configure:
        Runtime.from_dict(
            _base_config({"auto_configure": True, "level": "DEBUG"})
        )
    assert mock_configure.call_count == 1
    cfg = mock_configure.call_args.args[0]
    assert cfg.level == "DEBUG"


def test_auto_configure_false_does_not_call_configure() -> None:
    with patch(
        "openagents.observability.logging.configure", autospec=True
    ) as mock_configure:
        Runtime.from_dict(_base_config({"auto_configure": False}))
    assert mock_configure.call_count == 0


def test_no_logging_section_does_not_call_configure() -> None:
    with patch(
        "openagents.observability.logging.configure", autospec=True
    ) as mock_configure:
        Runtime.from_dict(_base_config())
    assert mock_configure.call_count == 0


def test_auto_configure_actually_sets_level_end_to_end() -> None:
    Runtime.from_dict(
        _base_config({"auto_configure": True, "level": "DEBUG"})
    )
    assert logging.getLogger("openagents").level == logging.DEBUG
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_runtime_auto_configure.py -v`
Expected: FAIL — `configure` never called because no hook exists yet.

- [ ] **Step 3: Add the hook to `Runtime.__init__`**

In `openagents/runtime/runtime.py`, at the end of `Runtime.__init__` (after the `components = load_runtime_components(...)` block, outside the `if _skip_plugin_load:` branch but inside `__init__`), add:

```python
        self._maybe_auto_configure_logging(config)
```

Then add the method to the `Runtime` class (place it right after `__init__`):

```python
    @staticmethod
    def _maybe_auto_configure_logging(config: AppConfig) -> None:
        """Opt-in hook: apply observability.configure() when the config requests it.

        Library etiquette: never auto-configure unless the config explicitly
        sets ``logging.auto_configure: true`` (or ``OPENAGENTS_LOG_AUTOCONFIGURE=1``
        overrides it).
        """
        from openagents.observability.config import merge_env_overrides
        from openagents.observability.logging import configure

        logging_cfg = config.logging
        if logging_cfg is None:
            # Still honor env-var-only activation.
            from openagents.observability.config import load_from_env

            env_cfg = load_from_env()
            if env_cfg is None or not env_cfg.auto_configure:
                return
            configure(env_cfg)
            return
        effective = merge_env_overrides(logging_cfg)
        if not effective.auto_configure:
            return
        configure(effective)
```

- [ ] **Step 4: Run integration test**

Run: `uv run pytest tests/integration/test_runtime_auto_configure.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run unit tests to make sure nothing regressed**

Run: `uv run pytest tests/unit -v --tb=short`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/runtime/runtime.py tests/integration/test_runtime_auto_configure.py
git commit -m "feat(runtime): opt-in logging auto-configure hook in Runtime.__init__"
```

---

## Task 13: Env-override integration test

**Files:**
- Test: `tests/integration/test_env_override.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_env_override.py`:

```python
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
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_env_override.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_env_override.py
git commit -m "test(observability): integration coverage for env-var override semantics"
```

---

## Task 14: Update example `quickstart/agent.json`

**Files:**
- Modify: `examples/quickstart/agent.json`

- [ ] **Step 1: Read current file**

Run: `cat examples/quickstart/agent.json`
Take note of existing keys so the `logging` insertion is non-destructive.

- [ ] **Step 2: Add a `logging` section**

At the top level of the JSON (alongside `runtime`/`session`/`events`/`agents`), insert:

```json
"logging": {
  "auto_configure": true,
  "pretty": true,
  "level": "INFO"
},
```

- [ ] **Step 3: Smoke-run the example**

Run: `uv run python examples/quickstart/run_demo.py`
Expected: the demo runs with colorized log output (assuming `MINIMAX_API_KEY` is set). If the key is missing, the demo's existing behavior is unchanged (plain error from the LLM call).

- [ ] **Step 4: Commit**

```bash
git add examples/quickstart/agent.json
git commit -m "docs(examples): enable pretty logging in quickstart agent.json"
```

---

## Task 15: Update docs

**Files:**
- Modify: `docs/developer-guide.md`
- Modify: `docs/configuration.md`
- Modify: `docs/seams-and-extension-points.md`

- [ ] **Step 1: `docs/developer-guide.md` — add new section**

Append at the end of the file:

```markdown
## 调试与可观测性

SDK 提供两条调试输出通道，分别对应"代码执行日志"和"运行时事件流"：

### 1. Python stdlib 日志（`openagents.*` 命名空间）

通过 `openagents.observability.configure()` 统一装配 handler/filter。默认**不自动生效** —— 嵌入宿主 app 时不会污染宿主的 logging 配置。

**启用方式**：

```python
from openagents.observability import configure, LoggingConfig

configure(LoggingConfig(level="DEBUG", pretty=True))
```

或在 `agent.json` 里配：

```json
{
  "logging": {
    "auto_configure": true,
    "level": "INFO",
    "per_logger_levels": {"openagents.llm": "DEBUG"},
    "pretty": true,
    "redact_keys": ["api_key", "authorization"]
  }
}
```

**环境变量覆盖**（CI / 临时调试）：

| 变量 | 示例 |
|---|---|
| `OPENAGENTS_LOG_AUTOCONFIGURE` | `1` |
| `OPENAGENTS_LOG_LEVEL` | `DEBUG` |
| `OPENAGENTS_LOG_LEVELS` | `openagents.llm=DEBUG,openagents.events=INFO` |
| `OPENAGENTS_LOG_PRETTY` | `1` |
| `OPENAGENTS_LOG_STREAM` | `stderr` |
| `OPENAGENTS_LOG_INCLUDE` | `openagents.llm,openagents.events` |
| `OPENAGENTS_LOG_EXCLUDE` | `openagents.events.file_logging` |
| `OPENAGENTS_LOG_REDACT` | `api_key,authorization` |
| `OPENAGENTS_LOG_MAX_VALUE_LENGTH` | `500` |

**`pretty: true` 要求装 `[rich]` extra**：`pip install io-openagent-sdk[rich]`。没装会抛 `RichNotInstalledError`。

### 2. 运行时事件流

除了已有的 `file_logging`（写 NDJSON）和 `otel_bridge`（导出 OTel span），现在还可以用 `rich_console` 把事件直接在终端漂亮打印：

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {"type": "async"},
      "include_events": ["tool.*", "llm.succeeded"],
      "show_payload": true,
      "redact_keys": ["api_key"]
    }
  }
}
```

三者都是 `EventBusPlugin` 包装器，可以通过 `inner` 字段叠加（例如先 `rich_console` 再 `file_logging` 再 `async`）。
```

- [ ] **Step 2: `docs/configuration.md` — document `logging` section**

Find the part that documents top-level sections (`runtime`, `session`, `events`, `skills`) and add a new subsection:

```markdown
### `logging`（可选）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `auto_configure` | bool | `false` | 是否让 `Runtime.__init__` 自动调 `configure()` |
| `level` | str | `"INFO"` | `openagents.*` 根 level |
| `per_logger_levels` | dict[str, str] | `{}` | 按 logger 名覆盖 level，如 `{"openagents.llm": "DEBUG"}` |
| `pretty` | bool | `false` | 启用 rich 渲染（需要 `[rich]` extra） |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | 输出流 |
| `include_prefixes` | list[str] \| null | `null` | logger 白名单（`null` = 允许所有） |
| `exclude_prefixes` | list[str] | `[]` | logger 黑名单 |
| `redact_keys` | list[str] | `["api_key", "authorization", "token", "secret", "password"]` | 脱敏 key 名（大小写不敏感） |
| `max_value_length` | int | `500` | 字符串 value 截断长度 |
| `show_time` | bool | `true` | 是否显示时间列（rich 模式） |
| `show_path` | bool | `false` | 是否显示代码路径（rich 模式） |

如果该 section 缺失或 `auto_configure=false`，SDK 不会修改任何 logging 配置。
```

- [ ] **Step 3: `docs/seams-and-extension-points.md` — add `rich_console` to events seam**

Find the `events` seam entry and add `rich_console` alongside `file_logging`/`otel_bridge`:

```markdown
- **`events`**：
  - `async`（默认，内存）
  - `file_logging`（NDJSON 落盘）
  - `otel_bridge`（OpenTelemetry span，需要 `[otel]` extra）
  - `rich_console`（终端漂亮打印，需要 `[rich]` extra）
```

- [ ] **Step 4: Commit**

```bash
git add docs/developer-guide.md docs/configuration.md docs/seams-and-extension-points.md
git commit -m "docs: cover logging config, rich extra, and rich_console event bus"
```

---

## Task 16: Final green + coverage check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Run coverage**

Run: `uv run coverage run -m pytest && uv run coverage report`
Expected: `TOTAL` coverage ≥ 92% (project floor); `openagents/observability/*` and `openagents/plugins/builtin/events/rich_console.py` fully covered or close to it.

- [ ] **Step 3: Inspect coverage for new files**

Run: `uv run coverage report --include="openagents/observability/*,openagents/plugins/builtin/events/rich_console.py"`
Expected: each file ≥ 90% covered. If a file falls below, add a targeted test before proceeding.

- [ ] **Step 4: If everything green, final summary commit (optional)**

No code changes required; this task is a verification gate. If coverage gaps show up, add tests and commit as `test(observability): cover <file>` before closing the branch.

---

## Post-implementation checklist

Run this list before merging:

- [ ] `uv run pytest -q` all green
- [ ] `uv run coverage report` ≥ 92%
- [ ] `uv run python examples/quickstart/run_demo.py` prints colorized output (with `MINIMAX_API_KEY` set)
- [ ] `OPENAGENTS_LOG_AUTOCONFIGURE=1 OPENAGENTS_LOG_LEVEL=DEBUG uv run python examples/quickstart/run_demo.py` shows DEBUG-level stdlib logs
- [ ] `uv pip install io-openagent-sdk` (without `[rich]`) + `pretty: true` raises `RichNotInstalledError` with install hint
- [ ] `docs/seams-and-extension-points.md` lists `rich_console`
- [ ] `MEMORY.md` unchanged (this work is scoped by the spec, not a long-lived preference)
