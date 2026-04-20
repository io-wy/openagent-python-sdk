from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus
from openagents.plugins.registry import get_builtin_plugin_class


def _make(tmp_path: Path, **overrides) -> FileLoggingEventBus:
    cfg = {"inner": {"type": "async"}, "log_path": str(tmp_path / "events.ndjson")}
    cfg.update(overrides)
    return FileLoggingEventBus(config=cfg)


@pytest.mark.asyncio
async def test_emit_forwards_to_inner_and_writes_line(tmp_path: Path):
    bus = _make(tmp_path)
    captured: list[str] = []

    async def handler(event):
        captured.append(event.name)

    bus.subscribe("tick", handler)
    await bus.emit("tick", n=1)
    assert captured == ["tick"]

    lines = (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["name"] == "tick"
    assert parsed["payload"]["n"] == 1


@pytest.mark.asyncio
async def test_include_events_filters(tmp_path: Path):
    bus = _make(tmp_path, include_events=["keep"])
    await bus.emit("drop", k=1)
    await bus.emit("keep", k=2)
    lines = (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines()
    names = [json.loads(line)["name"] for line in lines]
    assert names == ["keep"]


@pytest.mark.asyncio
async def test_non_serializable_payload_fallbacks(tmp_path: Path):
    bus = _make(tmp_path)

    class Weird:
        def __repr__(self):
            return "<Weird>"

    await bus.emit("x", obj=Weird())
    parsed = json.loads((tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines()[0])
    assert parsed["payload"]["obj"] == "<Weird>"


@pytest.mark.asyncio
async def test_write_failure_does_not_break_emit(tmp_path: Path, monkeypatch):
    bus = _make(tmp_path)
    captured: list[str] = []

    async def handler(event):
        captured.append(event.name)

    bus.subscribe("x", handler)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    await bus.emit("x")  # must not raise
    assert captured == ["x"]


@pytest.mark.asyncio
async def test_emit_recreates_log_dir_if_removed_after_init(tmp_path: Path):
    bus = _make(tmp_path / "nested")
    log_dir = tmp_path / "nested"
    shutil.rmtree(log_dir)

    await bus.emit("tick", n=1)

    log_path = log_dir / "events.ndjson"
    assert log_path.exists()
    parsed = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["name"] == "tick"
    assert parsed["payload"]["n"] == 1


@pytest.mark.asyncio
async def test_get_history_passthrough(tmp_path: Path):
    bus = _make(tmp_path)
    await bus.emit("a", n=1)
    await bus.emit("b", n=2)
    history = await bus.get_history()
    assert [e.name for e in history] == ["a", "b"]


@pytest.mark.asyncio
async def test_clear_history_passthrough(tmp_path: Path):
    bus = _make(tmp_path)
    await bus.emit("a")
    await bus.clear_history()
    assert (await bus.get_history()) == []


@pytest.mark.asyncio
async def test_max_history_forwarded_to_inner(tmp_path: Path):
    """Wrapper's max_history must propagate to the inner async bus."""
    bus = _make(tmp_path, max_history=3)
    # Emit more events than the inner history should retain.
    for i in range(5):
        await bus.emit("tick", i=i)
    history = await bus.get_history()
    assert len(history) == 3
    assert [e.payload["i"] for e in history] == [2, 3, 4]


@pytest.mark.asyncio
async def test_explicit_inner_max_history_wins(tmp_path: Path):
    """If the inner config explicitly sets max_history, wrapper does not override."""
    bus = FileLoggingEventBus(
        config={
            "inner": {"type": "async", "config": {"max_history": 2}},
            "log_path": str(tmp_path / "events.ndjson"),
            "max_history": 100,
        }
    )
    for i in range(4):
        await bus.emit("tick", i=i)
    history = await bus.get_history()
    assert len(history) == 2


def test_registered_as_builtin():
    assert get_builtin_plugin_class("events", "file_logging") is FileLoggingEventBus
