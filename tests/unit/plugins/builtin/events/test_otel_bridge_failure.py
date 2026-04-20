"""If start_as_current_span raises, inner emit still completes; failure logged."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("opentelemetry")
pytest.importorskip("opentelemetry.sdk")

from openagents.plugins.builtin.events.otel_bridge import OtelEventBusBridge


@pytest.mark.asyncio
async def test_inner_emit_runs_when_otel_raises(caplog):
    bus = OtelEventBusBridge(config={})

    captured: list[str] = []

    async def handler(event):
        captured.append(event.name)

    bus.subscribe("custom.event", handler)

    def boom(*args, **kwargs):
        raise RuntimeError("OTel SDK exploded")

    bus._tracer.start_as_current_span = boom  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR, logger="openagents.events.otel_bridge"):
        event = await bus.emit("custom.event", k=1)

    # Inner subscriber received the event.
    assert captured == ["custom.event"]
    assert event.name == "custom.event"
    # Inner history reflects the emit too.
    history = await bus.get_history()
    assert [e.name for e in history] == ["custom.event"]
    # The failure was logged with our marker prefix.
    assert any("otel_bridge: failed to emit" in record.getMessage() for record in caplog.records), [
        r.getMessage() for r in caplog.records
    ]
