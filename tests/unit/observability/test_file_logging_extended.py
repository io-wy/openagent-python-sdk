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


@pytest.mark.asyncio
async def test_history_property_passthrough(tmp_path: Path) -> None:
    log = tmp_path / "events.ndjson"
    bus = FileLoggingEventBus(
        config={"log_path": str(log), "inner": {"type": "async"}}
    )
    await bus.emit("tool.called", x=1)
    await bus.emit("tool.succeeded", x=1)
    assert [e.name for e in bus.history] == ["tool.called", "tool.succeeded"]
