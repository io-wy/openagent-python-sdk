"""include_events with fnmatch wildcards filters span emission only."""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")
pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from openagents.plugins.builtin.events.otel_bridge import OtelEventBusBridge


@pytest.fixture
def exporter_and_provider():
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    yield exp, provider
    exp.clear()


@pytest.mark.asyncio
async def test_wildcard_include_only_matched_events_emit_spans(exporter_and_provider):
    exporter, provider = exporter_and_provider
    bus = OtelEventBusBridge(config={"include_events": ["tool.*"]})
    bus._tracer = provider.get_tracer("openagents.test")

    await bus.emit("tool.called", tool_id="x", params={"q": "x"})
    await bus.emit("tool.succeeded", tool_id="x", result="r")
    await bus.emit("llm.called", model="m1")

    spans = exporter.get_finished_spans()
    names = sorted(s.name for s in spans)
    assert names == ["openagents.tool.called", "openagents.tool.succeeded"]

    # Inner bus must still see all three events.
    history = await bus.get_history()
    inner_names = sorted(e.name for e in history)
    assert inner_names == ["llm.called", "tool.called", "tool.succeeded"]
