from __future__ import annotations

from openagents.interfaces.diagnostics import LLMCallMetrics
from openagents.interfaces.runtime import RunResult, RunUsage
from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin


def test_null_record_llm_call_no_op():
    plugin = NullDiagnosticsPlugin()
    m = LLMCallMetrics(model="x", latency_ms=1.0, input_tokens=1, output_tokens=1, cached_tokens=0)
    plugin.record_llm_call("run-1", m)


def test_null_capture_error_snapshot_returns_snapshot():
    plugin = NullDiagnosticsPlugin()
    try:
        raise ValueError("oops")
    except ValueError as exc:
        snap = plugin.capture_error_snapshot(
            run_id="r1",
            agent_id="a1",
            session_id="s1",
            exc=exc,
            ctx=None,
            usage=None,
        )
    assert snap.run_id == "r1"
    assert snap.error_type == "ValueError"
    assert snap.tool_call_chain == []
    assert snap.last_transcript == []


def test_null_on_run_complete_no_op():
    plugin = NullDiagnosticsPlugin()
    result = RunResult(run_id="r1", usage=RunUsage())
    plugin.on_run_complete(result, None)


def test_null_get_run_metrics_empty():
    plugin = NullDiagnosticsPlugin()
    assert plugin.get_run_metrics("run-1") == {}
