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
    from io import StringIO

    from rich.console import Console as RichConsole

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


@pytest.mark.asyncio
async def test_history_property_passthrough() -> None:
    bus, _ = _make_bus()
    await bus.emit("tool.called", x=1)
    await bus.emit("tool.succeeded", x=1)
    # Property delegates to inner bus's sync .history buffer (same list, same ordering)
    assert [e.name for e in bus.history] == ["tool.called", "tool.succeeded"]


def test_registered_in_builtin_registry() -> None:
    from openagents.plugins.registry import get_builtin_plugin_class

    cls = get_builtin_plugin_class("events", "rich_console")
    assert cls is RichConsoleEventBus


@pytest.mark.asyncio
async def test_single_line_render_when_show_payload_false() -> None:
    bus, console = _make_bus(show_payload=False)
    await bus.emit("tool.called", agent_id="a1", tool="bash")

    from io import StringIO

    from rich.console import Console as RichConsole

    buf = StringIO()
    real = RichConsole(file=buf, force_terminal=False, highlight=False)
    real.print(console.rendered[0])
    rendered = buf.getvalue()
    # Single-line renderer emits 'name  key=value key=value' on one line
    assert "tool.called" in rendered
    assert "agent_id=" in rendered
    assert "tool=" in rendered
    # Panel renderer would add box-drawing characters like '╭' or '┌'
    assert "╭" not in rendered and "┌" not in rendered
