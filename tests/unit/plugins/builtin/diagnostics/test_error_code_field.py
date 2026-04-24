from __future__ import annotations

from openagents.errors.exceptions import ToolTimeoutError
from openagents.interfaces.diagnostics import DiagnosticsPlugin


def test_capture_error_snapshot_sets_error_code():
    plugin = DiagnosticsPlugin()
    snap = plugin.capture_error_snapshot(
        run_id="r",
        agent_id="a",
        session_id="s",
        exc=ToolTimeoutError("slow", tool_name="x"),
    )
    assert snap.error_code == "tool.timeout"


def test_capture_error_snapshot_falls_back_for_non_openagents_error():
    plugin = DiagnosticsPlugin()
    snap = plugin.capture_error_snapshot(run_id="r", agent_id="a", session_id="s", exc=ValueError("bad"))
    assert snap.error_code == "error.unknown"
