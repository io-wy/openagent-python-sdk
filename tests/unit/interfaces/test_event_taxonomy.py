"""Tests for tool-invocation-enhancement event taxonomy additions."""

from __future__ import annotations

from openagents.interfaces.event_taxonomy import EVENT_SCHEMAS


def test_tool_invocation_enhancement_events_declared():
    expected = {
        "tool.batch.started",
        "tool.batch.completed",
        "tool.approval_needed",
        "tool.cancelled",
        "tool.background.submitted",
        "tool.background.polled",
        "tool.background.completed",
    }
    missing = expected - set(EVENT_SCHEMAS.keys())
    assert not missing, f"missing declared events: {missing}"


def test_tool_batch_events_carry_expected_payload():
    started = EVENT_SCHEMAS["tool.batch.started"]
    assert "batch_id" in started.required_payload
    completed = EVENT_SCHEMAS["tool.batch.completed"]
    assert "batch_id" in completed.required_payload
    assert "successes" in completed.required_payload
    assert "failures" in completed.required_payload


def test_tool_approval_needed_carries_call_id_and_params():
    schema = EVENT_SCHEMAS["tool.approval_needed"]
    assert "tool_id" in schema.required_payload
    assert "call_id" in schema.required_payload
    assert "params" in schema.required_payload


def test_tool_called_schema_optionally_accepts_call_id():
    schema = EVENT_SCHEMAS["tool.called"]
    assert "call_id" in schema.optional_payload
