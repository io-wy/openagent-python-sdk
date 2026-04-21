from __future__ import annotations

import pytest

from openagents.interfaces.diagnostics import ErrorSnapshot, LLMCallMetrics
from openagents.interfaces.runtime import RunResult, RunUsage


def test_rich_plugin_success_panel_renders_tokens_and_latency(capsys):
    pytest.importorskip("rich")
    from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin

    plugin = RichDiagnosticsPlugin()
    plugin.record_llm_call(
        "r1",
        LLMCallMetrics(model="m", latency_ms=120.0, input_tokens=50, output_tokens=30, cached_tokens=0),
    )
    plugin.record_llm_call(
        "r1",
        LLMCallMetrics(model="m", latency_ms=350.0, input_tokens=40, output_tokens=20, cached_tokens=0),
    )

    result = RunResult(run_id="r1", usage=RunUsage(llm_calls=2, input_tokens=90, output_tokens=50))
    plugin.on_run_complete(result, None)

    # Percentiles should be back-filled into usage.
    assert result.usage.llm_latency_p50_ms is not None
    assert result.usage.llm_latency_p50_ms > 0

    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "r1" in output
    # Some latency text should be printed.
    assert "ms" in output or "latency" in output


def test_rich_plugin_failure_panel_includes_error_info(capsys):
    pytest.importorskip("rich")
    from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin

    plugin = RichDiagnosticsPlugin()
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ValueError",
        error_message="something broke",
        traceback="Traceback (most recent call last):\n  ...",
        tool_call_chain=[{"tool_id": "search", "params": {"q": "hi"}}],
        last_transcript=[],
        usage_at_failure={},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    result = RunResult(run_id="r1", usage=RunUsage(), stop_reason="failed")
    plugin.on_run_complete(result, snap)

    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "ValueError" in output
    assert "something broke" in output


def test_rich_plugin_cleans_up_per_run_state():
    pytest.importorskip("rich")
    from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin

    plugin = RichDiagnosticsPlugin()
    plugin.record_llm_call(
        "r1", LLMCallMetrics(model="m", latency_ms=100.0, input_tokens=1, output_tokens=1, cached_tokens=0)
    )
    result = RunResult(run_id="r1", usage=RunUsage())
    plugin.on_run_complete(result, None)

    # Subsequent on_run_complete for same run_id should have nothing left.
    assert plugin._per_run.get("r1") is None
