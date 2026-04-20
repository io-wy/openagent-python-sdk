"""Each emit produces one OTel span with the right name and oa.* attrs."""

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
    """Build a fresh TracerProvider with an in-memory exporter.

    set_tracer_provider() is one-shot in the OTel API, so we instead
    inject the test provider into the bridge instance after construction
    via ``provider.get_tracer(...)``.
    """
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    yield exp, provider
    exp.clear()


def _bind(bus: OtelEventBusBridge, provider: TracerProvider) -> None:
    bus._tracer = provider.get_tracer("openagents.test")


@pytest.mark.asyncio
async def test_each_emit_produces_one_named_span(exporter_and_provider):
    exporter, provider = exporter_and_provider
    bus = OtelEventBusBridge(config={})
    _bind(bus, provider)
    await bus.emit("tool.called", tool_id="search", params={"q": "x"})
    await bus.emit("tool.succeeded", tool_id="search", result=42)

    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "openagents.tool.called" in names
    assert "openagents.tool.succeeded" in names

    by_name = {s.name: s for s in spans}
    succ = by_name["openagents.tool.succeeded"]
    assert succ.attributes.get("oa.tool_id") == "search"
    # Numeric values are stringified.
    assert succ.attributes.get("oa.result") == "42"
