"""Tests for pattern.call_tool unwrap helper and event payload."""

from __future__ import annotations

from typing import Any

import pytest

from openagents.interfaces.events import RuntimeEvent
from openagents.interfaces.pattern import PatternPlugin, unwrap_tool_result
from openagents.interfaces.tool import ToolExecutionResult


def test_unwrap_tool_result_returns_data_and_metadata_for_tool_execution_result():
    result = ToolExecutionResult(
        tool_id="t",
        success=True,
        data={"value": 1},
        metadata={"retry_attempts": 2},
    )
    data, metadata = unwrap_tool_result(result)
    assert data == {"value": 1}
    assert metadata == {"retry_attempts": 2}


def test_unwrap_tool_result_returns_raw_with_none_metadata_for_plain_value():
    data, metadata = unwrap_tool_result({"plain": True})
    assert data == {"plain": True}
    assert metadata is None


def test_unwrap_tool_result_handles_empty_metadata():
    result = ToolExecutionResult(tool_id="t", success=True, data="x")
    data, metadata = unwrap_tool_result(result)
    assert data == "x"
    # Empty metadata becomes empty dict (per the helper contract:
    # dict(result.metadata or {}) — never None for ToolExecutionResult).
    assert metadata == {}


def test_unwrap_tool_result_metadata_is_a_copy():
    src = {"a": 1}
    result = ToolExecutionResult(tool_id="t", success=True, data=None, metadata=src)
    _, metadata = unwrap_tool_result(result)
    metadata["b"] = 2
    assert "b" not in src


class _EventBus:
    def __init__(self) -> None:
        self.history: list[RuntimeEvent] = []

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        ev = RuntimeEvent(name=event_name, payload=payload)
        self.history.append(ev)
        return ev


class _BoundLikeTool:
    """Mimics _BoundTool by returning a ToolExecutionResult."""

    async def invoke(self, params: dict[str, Any], ctx: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_id="t",
            success=True,
            data={"value": 99},
            metadata={"retry_attempts": 4},
        )


class _RawTool:
    """Returns plain data, like a raw ToolPlugin would."""

    async def invoke(self, params: dict[str, Any], ctx: Any) -> Any:
        return {"value": 7}


@pytest.mark.asyncio
async def test_pattern_call_tool_emits_executor_metadata_when_tool_returns_result():
    pattern = PatternPlugin()
    bus = _EventBus()
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="",
        state={},
        tools={"t": _BoundLikeTool()},
        llm_client=None,
        llm_options=None,
        event_bus=bus,
    )

    data = await pattern.call_tool("t", {})
    assert data == {"value": 99}

    succeeded = next(e for e in bus.history if e.name == "tool.succeeded")
    assert succeeded.payload["result"] == {"value": 99}
    assert succeeded.payload["executor_metadata"] == {"retry_attempts": 4}


@pytest.mark.asyncio
async def test_pattern_call_tool_emits_none_executor_metadata_for_raw_tool():
    pattern = PatternPlugin()
    bus = _EventBus()
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="",
        state={},
        tools={"t": _RawTool()},
        llm_client=None,
        llm_options=None,
        event_bus=bus,
    )

    data = await pattern.call_tool("t", {})
    assert data == {"value": 7}

    succeeded = next(e for e in bus.history if e.name == "tool.succeeded")
    assert succeeded.payload["result"] == {"value": 7}
    assert succeeded.payload["executor_metadata"] is None


@pytest.mark.asyncio
async def test_pattern_call_tool_records_unwrapped_data_in_tool_results():
    pattern = PatternPlugin()
    bus = _EventBus()
    await pattern.setup(
        agent_id="a",
        session_id="s",
        input_text="",
        state={},
        tools={"t": _BoundLikeTool()},
        llm_client=None,
        llm_options=None,
        event_bus=bus,
    )
    await pattern.call_tool("t", {})
    assert pattern.context.tool_results == [{"tool_id": "t", "result": {"value": 99}}]
