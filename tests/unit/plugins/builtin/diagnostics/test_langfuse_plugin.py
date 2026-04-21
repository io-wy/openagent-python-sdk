from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_langfuse_plugin_missing_import_raises(monkeypatch):
    """When langfuse is absent, constructing the plugin raises ImportError."""
    # Ensure any cached langfuse module is hidden.
    monkeypatch.setitem(sys.modules, "langfuse", None)

    from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter

    with pytest.raises(ImportError, match="langfuse"):
        LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})


def test_langfuse_plugin_on_run_complete_calls_trace():
    """on_run_complete sends a Langfuse trace for the run."""
    from openagents.interfaces.diagnostics import LLMCallMetrics
    from openagents.interfaces.runtime import RunResult, RunUsage

    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace

    fake_langfuse_module = SimpleNamespace(Langfuse=lambda **kwargs: mock_client)
    with patch.dict(sys.modules, {"langfuse": fake_langfuse_module}):
        from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter

        plugin = LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})
        plugin.record_llm_call(
            "r1",
            LLMCallMetrics(model="m", latency_ms=100.0, input_tokens=10, output_tokens=5, cached_tokens=0),
        )
        result = RunResult(run_id="r1", usage=RunUsage(llm_calls=1, input_tokens=10, output_tokens=5))
        plugin.on_run_complete(result, None)

    mock_client.trace.assert_called_once()
    call_kwargs = mock_client.trace.call_args.kwargs
    assert call_kwargs.get("id") == "r1"
    metadata = call_kwargs.get("metadata", {})
    assert metadata.get("llm_calls") == 1
    # A span per LLM call.
    assert mock_trace.span.call_count == 1


def test_langfuse_plugin_error_snapshot_in_metadata():
    """ErrorSnapshot contents appear in trace metadata when present."""
    from openagents.interfaces.diagnostics import ErrorSnapshot
    from openagents.interfaces.runtime import RunResult, RunUsage, StopReason

    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace

    fake_langfuse_module = SimpleNamespace(Langfuse=lambda **kwargs: mock_client)
    with patch.dict(sys.modules, {"langfuse": fake_langfuse_module}):
        from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter

        plugin = LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})
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

    call_kwargs = mock_client.trace.call_args.kwargs
    metadata = call_kwargs.get("metadata", {})
    assert "error_snapshot" in metadata
    assert metadata["error_snapshot"]["error_type"] == "ValueError"


def test_langfuse_plugin_back_fills_latency_percentiles():
    """After on_run_complete, result.usage has latency percentiles populated."""
    from openagents.interfaces.diagnostics import LLMCallMetrics
    from openagents.interfaces.runtime import RunResult, RunUsage

    mock_client = MagicMock()
    mock_client.trace.return_value = MagicMock()

    fake_langfuse_module = SimpleNamespace(Langfuse=lambda **kwargs: mock_client)
    with patch.dict(sys.modules, {"langfuse": fake_langfuse_module}):
        from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter

        plugin = LangfuseExporter(config={})
        for latency in (50.0, 100.0, 200.0, 400.0, 800.0):
            plugin.record_llm_call(
                "r1",
                LLMCallMetrics(model="m", latency_ms=latency, input_tokens=1, output_tokens=1, cached_tokens=0),
            )
        result = RunResult(run_id="r1", usage=RunUsage())
        plugin.on_run_complete(result, None)

    assert result.usage.llm_latency_p50_ms is not None
    assert result.usage.llm_latency_p95_ms is not None
    assert result.usage.llm_latency_p50_ms >= 100.0
    assert result.usage.llm_latency_p95_ms >= result.usage.llm_latency_p50_ms
