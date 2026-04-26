"""Tests for Task 6: runtime builds ErrorDetails on failure; event payloads carry error_details."""

from __future__ import annotations

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import ToolTimeoutError
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime

# ---------------------------------------------------------------------------
# Fixtures: a pattern that raises ToolTimeoutError mid-run via a tool
# ---------------------------------------------------------------------------


class _TimeoutRaisingTool:
    """Tool that always raises ToolTimeoutError."""

    def __init__(self, config=None):
        self.config = config or {}

    async def invoke(self, params, context):
        raise ToolTimeoutError("slow", tool_name="search")

    async def fallback(self, error, params, context):
        raise error

    def describe(self):
        return {"name": "search", "description": "always times out", "parameters": {"type": "object"}}

    def schema(self):
        return {"type": "object", "properties": {}, "required": []}


class _ToolTimeoutPattern:
    """Pattern that calls the 'search' tool (which always times out)."""

    def __init__(self, config=None):
        self.config = config or {}
        self.context = None

    async def setup(self, agent_id, session_id, input_text, state, tools, llm_client, llm_options, event_bus, **_):
        from openagents.interfaces.pattern import ExecutionContext

        self.context = ExecutionContext(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
        )

    async def react(self):
        return {"type": "tool_call", "tool": "search", "params": {}}

    async def execute(self):
        tool = self.context.tools["search"]
        await tool.invoke({}, self.context)
        return "done"


def _config_with_timeout_tool() -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "timeout-test-agent",
                "memory": {"impl": "openagents.plugins.builtin.memory.buffer.BufferMemory", "on_error": "continue"},
                "pattern": {"impl": "tests.unit.runtime.test_error_details_emission._ToolTimeoutPattern"},
                "llm": {"provider": "mock"},
                "tools": [
                    {
                        "id": "search",
                        "impl": "tests.unit.runtime.test_error_details_emission._TimeoutRaisingTool",
                    }
                ],
                "runtime": {
                    "max_steps": 3,
                    "step_timeout_ms": 5000,
                    "session_queue_size": 10,
                    "event_queue_size": 10,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_result_has_error_details_on_tool_timeout():
    """RunResult.error_details is populated when a ToolTimeoutError fires."""
    runtime = Runtime(load_config_dict(_config_with_timeout_tool()))

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-timeout",
            input_text="search for something",
        )
    )

    assert result.stop_reason == StopReason.FAILED.value
    assert result.error_details is not None
    assert result.error_details.code == "tool.timeout"
    assert result.error_details.retryable is True

    await runtime.close()


@pytest.mark.asyncio
async def test_run_failed_event_carries_error_details_dict():
    """run.failed event payload contains error_details with code == 'tool.timeout'."""
    runtime = Runtime(load_config_dict(_config_with_timeout_tool()))

    await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-event",
            input_text="go",
        )
    )

    run_failed_events = [evt for evt in runtime.event_bus.history if evt.name == "run.failed"]
    assert len(run_failed_events) == 1, f"Expected 1 run.failed event, got {len(run_failed_events)}"

    payload = run_failed_events[0].payload or {}
    # Legacy field must be preserved for backward compat
    assert "error" in payload
    assert isinstance(payload["error"], str)

    # New structured field
    assert "error_details" in payload, f"run.failed payload missing error_details: {payload.keys()}"
    ed = payload["error_details"]
    assert isinstance(ed, dict)
    assert ed.get("code") == "tool.timeout"
    assert ed.get("retryable") is True

    await runtime.close()


@pytest.mark.asyncio
async def test_error_details_retryable_flag_set_for_timeout():
    """ErrorDetails.retryable reflects ToolTimeoutError.retryable = True."""
    runtime = Runtime(load_config_dict(_config_with_timeout_tool()))

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-retryable",
            input_text="go",
        )
    )

    assert result.error_details is not None
    assert result.error_details.retryable is True

    await runtime.close()


@pytest.mark.asyncio
async def test_error_details_on_validation_exhausted():
    """Validation-exhausted path also populates error_details."""
    from pydantic import BaseModel

    from openagents.config.loader import load_config_dict as _lcd

    class _StrictOutput(BaseModel):
        value: int

    # QueuedRawOutputPattern always returns non-conforming strings so validation always fails.
    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "retry-test-agent",
                "memory": {
                    "impl": "openagents.plugins.builtin.memory.buffer.BufferMemory",
                    "on_error": "continue",
                },
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.QueuedRawOutputPattern",
                    "config": {"responses": ["bad", "bad", "bad", "bad", "bad"]},
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 10,
                    "step_timeout_ms": 5000,
                    "session_queue_size": 10,
                    "event_queue_size": 10,
                },
            }
        ],
    }
    runtime = Runtime(_lcd(payload))

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-val-exhausted",
            input_text="go",
            output_type=_StrictOutput,
            budget=RunBudget(max_validation_retries=2),
        )
    )

    assert result.stop_reason == StopReason.FAILED.value
    assert result.error_details is not None
    # OutputValidationError → code "execution.output_validation" per spec §1.2
    assert result.error_details.code == "execution.output_validation"
    assert result.error_details.retryable is False

    await runtime.close()


@pytest.mark.asyncio
async def test_memory_inject_failed_event_carries_error_details():
    """memory.inject_failed event payload contains error_details when inject raises."""
    from openagents.config.loader import load_config_dict as _lcd

    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "inject-fail-agent",
                "memory": {
                    "impl": "tests.fixtures.runtime_plugins.FailingInjectMemory",
                    "on_error": "continue",
                },
                "pattern": {"impl": "tests.fixtures.runtime_plugins.FinalPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 3,
                    "step_timeout_ms": 5000,
                    "session_queue_size": 10,
                    "event_queue_size": 10,
                },
            }
        ],
    }
    runtime = Runtime(_lcd(payload))

    await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-inject-fail",
            input_text="go",
        )
    )

    inject_failed_events = [evt for evt in runtime.event_bus.history if evt.name == "memory.inject_failed"]
    assert len(inject_failed_events) == 1

    payload_data = inject_failed_events[0].payload or {}
    assert "error" in payload_data
    assert "error_details" in payload_data, f"memory.inject_failed missing error_details: {payload_data.keys()}"
    ed = payload_data["error_details"]
    assert isinstance(ed, dict)
    assert "code" in ed

    await runtime.close()
