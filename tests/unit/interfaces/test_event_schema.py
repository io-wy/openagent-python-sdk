"""WP2: AsyncEventBus emits a warning when a declared event is missing required payload."""

from __future__ import annotations

import logging

import pytest

from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus


@pytest.mark.asyncio
async def test_warns_when_required_key_missing(caplog):
    bus = AsyncEventBus()
    with caplog.at_level(logging.WARNING, logger="openagents"):
        # tool.called requires 'tool_id' and 'params'; omit 'params'
        await bus.emit("tool.called", tool_id="x")
    assert any(
        "tool.called" in record.message and "params" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_emit_succeeds_even_with_missing_payload(caplog):
    bus = AsyncEventBus()
    event = await bus.emit("tool.called", tool_id="x")
    assert event.name == "tool.called"
    assert event.payload == {"tool_id": "x"}
    history = await bus.get_history()
    assert len(history) == 1


@pytest.mark.asyncio
async def test_no_warning_when_all_required_present(caplog):
    bus = AsyncEventBus()
    with caplog.at_level(logging.WARNING, logger="openagents"):
        await bus.emit("tool.called", tool_id="x", params={})
    assert not any(
        "missing required payload" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_unknown_event_name_is_not_validated(caplog):
    bus = AsyncEventBus()
    with caplog.at_level(logging.WARNING, logger="openagents"):
        await bus.emit("custom.user.event")  # nothing required, no warning
    assert not any(
        "missing required payload" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_optional_payload_missing_is_not_warning(caplog):
    bus = AsyncEventBus()
    with caplog.at_level(logging.WARNING, logger="openagents"):
        # tool.succeeded has 'tool_id'/'result' required, 'executor_metadata' optional
        await bus.emit("tool.succeeded", tool_id="x", result="ok")
    assert not any(
        "missing required payload" in record.message
        for record in caplog.records
    )
