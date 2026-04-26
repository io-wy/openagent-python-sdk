from __future__ import annotations

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


def test_capture_error_snapshot_traceback_uses_exc_not_current_handler():
    """Regression: ``capture_error_snapshot`` previously used
    ``traceback.format_exc()``, which formats the *currently handled*
    exception from ``sys.exc_info()``. That produced an empty or wrong
    traceback whenever the helper was called outside an ``except:``
    block, or while a *different* exception was being handled (e.g.,
    diagnostics captured during cleanup that itself raised).

    The fix formats the passed-in ``exc`` directly, so the snapshot is
    self-contained and independent of ``sys.exc_info()`` state.
    """
    plugin = DiagnosticsPlugin()

    # Build an exception with a real traceback, then let it escape the
    # except block so sys.exc_info() goes back to (None, None, None).
    captured: ValueError | None = None
    try:
        raise ValueError("boom-outer")
    except ValueError as exc:  # noqa: BLE001
        captured = exc
    assert captured is not None and captured.__traceback__ is not None

    # Now an unrelated exception is being handled — the naive
    # ``format_exc()`` would serialize *this* one instead of boom-outer.
    try:
        raise RuntimeError("unrelated-currently-handled")
    except RuntimeError:
        snap = plugin.capture_error_snapshot(
            run_id="r1",
            agent_id="a1",
            session_id="s1",
            exc=captured,
            ctx=None,
            usage=None,
        )

    assert snap.error_type == "ValueError"
    assert snap.error_message == "boom-outer"
    # Traceback must describe the passed exception, not the surrounding one.
    assert "boom-outer" in snap.traceback
    assert "ValueError" in snap.traceback
    assert "unrelated-currently-handled" not in snap.traceback


def test_capture_error_snapshot_traceback_outside_except_block():
    """Even when nothing is currently being handled, formatting from the
    passed ``exc`` must still produce a non-empty traceback (given that
    the exception carries a ``__traceback__``)."""
    plugin = DiagnosticsPlugin()

    try:
        raise KeyError("missing-key")
    except KeyError as exc:  # noqa: BLE001
        captured = exc

    # Call the helper after the except block has exited.
    snap = plugin.capture_error_snapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        exc=captured,
        ctx=None,
        usage=None,
    )
    assert snap.error_type == "KeyError"
    assert "missing-key" in snap.traceback
    assert "KeyError" in snap.traceback
