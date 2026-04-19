"""Long string attributes are truncated to max_attribute_chars + '...[truncated]'."""

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
async def test_long_string_payload_is_truncated(exporter_and_provider):
    exporter, provider = exporter_and_provider
    cap = 64
    bus = OtelEventBusBridge(config={"max_attribute_chars": cap})
    bus._tracer = provider.get_tracer("openagents.test")

    long_text = "a" * (cap * 4)
    await bus.emit("custom.event", blob=long_text)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    blob = spans[0].attributes.get("oa.blob")
    assert isinstance(blob, str)
    assert blob.endswith("...[truncated]")
    # cap chars + literal '...[truncated]' suffix == cap + 14
    assert len(blob) == cap + len("...[truncated]")
    assert len(blob) <= cap + 14
