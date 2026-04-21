from __future__ import annotations

from openagents.interfaces.capabilities import DIAG_ERROR, DIAG_EXPORT, DIAG_METRICS
from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)


def test_llm_call_metrics_defaults():
    m = LLMCallMetrics(
        model="claude-3-5-sonnet",
        latency_ms=120.5,
        input_tokens=50,
        output_tokens=30,
        cached_tokens=0,
    )
    assert m.ttft_ms is None
    assert m.attempt == 1
    assert m.error is None


def test_llm_call_metrics_with_ttft():
    m = LLMCallMetrics(
        model="claude-3-5-sonnet",
        ttft_ms=45.2,
        latency_ms=320.0,
        input_tokens=100,
        output_tokens=80,
        cached_tokens=20,
        attempt=2,
    )
    assert m.ttft_ms == 45.2
    assert m.attempt == 2


def test_error_snapshot_fields():
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ValueError",
        error_message="bad input",
        traceback="Traceback...",
        tool_call_chain=[{"tool_id": "t1", "params": {}}],
        last_transcript=[{"role": "user", "content": "hi"}],
        usage_at_failure={"llm_calls": 2},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    assert snap.run_id == "r1"
    assert len(snap.tool_call_chain) == 1


def test_error_snapshot_empty_chain_for_degraded():
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ConfigError",
        error_message="bad cfg",
        traceback="",
        tool_call_chain=[],
        last_transcript=[],
        usage_at_failure={},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    assert snap.tool_call_chain == []
    assert snap.last_transcript == []


def test_base_plugin_record_llm_call_no_op():
    plugin = DiagnosticsPlugin()
    m = LLMCallMetrics(model="x", latency_ms=1.0, input_tokens=1, output_tokens=1, cached_tokens=0)
    plugin.record_llm_call("run-1", m)
    assert plugin.get_run_metrics("run-1") == {}


def test_base_plugin_capture_error_snapshot_without_ctx():
    plugin = DiagnosticsPlugin()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        snap = plugin.capture_error_snapshot(
            run_id="r1",
            agent_id="a1",
            session_id="s1",
            exc=exc,
            ctx=None,
            usage=None,
        )
    assert snap.error_type == "ValueError"
    assert snap.error_message == "boom"
    assert snap.tool_call_chain == []
    assert snap.last_transcript == []
    assert snap.state_snapshot == {}
    assert snap.usage_at_failure == {}
    assert snap.captured_at.endswith("+00:00") or snap.captured_at.endswith("Z")


def test_capability_constants():
    assert DIAG_METRICS == "diagnostics.metrics"
    assert DIAG_ERROR == "diagnostics.error"
    assert DIAG_EXPORT == "diagnostics.export"
