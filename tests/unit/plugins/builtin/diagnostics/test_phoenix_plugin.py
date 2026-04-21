from __future__ import annotations

import sys

import pytest


def test_phoenix_plugin_missing_import_raises(monkeypatch):
    """When opentelemetry is absent, constructing the plugin raises ImportError."""
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)

    from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter

    with pytest.raises(ImportError):
        PhoenixExporter()


def _new_provider_and_exporter():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_phoenix_plugin_on_run_complete_records_spans():
    """on_run_complete produces a root span and one child span per LLM call."""
    pytest.importorskip("opentelemetry")

    from openagents.interfaces.diagnostics import LLMCallMetrics
    from openagents.interfaces.runtime import RunResult, RunUsage
    from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter

    provider, exporter = _new_provider_and_exporter()
    plugin = PhoenixExporter(config={"tracer_provider": provider})
    plugin.record_llm_call(
        "r1",
        LLMCallMetrics(model="m1", latency_ms=100.0, input_tokens=5, output_tokens=3, cached_tokens=0),
    )
    plugin.record_llm_call(
        "r1",
        LLMCallMetrics(model="m2", latency_ms=200.0, input_tokens=7, output_tokens=4, cached_tokens=1),
    )
    result = RunResult(run_id="r1", usage=RunUsage(llm_calls=2, input_tokens=12, output_tokens=7))
    plugin.on_run_complete(result, None)

    spans = exporter.get_finished_spans()
    span_names = {s.name for s in spans}
    assert "openagents.run" in span_names
    assert any(n.startswith("openagents.llm.call.") for n in span_names)
    assert result.usage.llm_latency_p50_ms is not None


def test_phoenix_plugin_sets_error_attributes_on_failure():
    pytest.importorskip("opentelemetry")

    from openagents.interfaces.diagnostics import ErrorSnapshot
    from openagents.interfaces.runtime import RunResult, RunUsage, StopReason
    from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter

    provider, exporter = _new_provider_and_exporter()
    plugin = PhoenixExporter(config={"tracer_provider": provider})
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ValueError",
        error_message="oops",
        traceback="",
        tool_call_chain=[],
        last_transcript=[],
        usage_at_failure={},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    result = RunResult(run_id="r1", stop_reason=StopReason.FAILED, usage=RunUsage())
    plugin.on_run_complete(result, snap)

    spans = exporter.get_finished_spans()
    root_spans = [s for s in spans if s.name == "openagents.run"]
    assert len(root_spans) == 1
    attrs = dict(root_spans[0].attributes or {})
    assert attrs.get("error.type") == "ValueError"


def test_phoenix_plugin_uses_global_provider_when_no_override():
    """Without tracer_provider in config, plugin falls back to the OTel global."""
    pytest.importorskip("opentelemetry")

    from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter

    plugin = PhoenixExporter()  # no config
    assert plugin._tracer_provider is None
