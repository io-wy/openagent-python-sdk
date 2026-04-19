"""Tests for _BoundTool.invoke metadata passthrough.

Policy evaluation is the executor's responsibility (see
``test_tool_executor_evaluate_policy.py``). _BoundTool itself no longer
pre-checks policy — it just dispatches through the executor and
propagates the resulting :class:`ToolExecutionResult`.
"""

from __future__ import annotations

from typing import Any

import pytest

from openagents.errors.exceptions import ToolError
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)
from openagents.plugins.builtin.runtime.default_runtime import _BoundTool


class _MetadataExecutor(ToolExecutorPlugin):
    """Executor that returns a ToolExecutionResult with metadata."""

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=True,
            data={"value": 42},
            metadata={"retry_attempts": 3, "timeout_ms": 1000},
        )

    async def execute_stream(self, request: ToolExecutionRequest):
        if False:
            yield {}


class _FailingExecutor(ToolExecutorPlugin):
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        exc = ToolError("boom", tool_name=request.tool_id)
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=False,
            error="boom",
            exception=exc,
        )

    async def execute_stream(self, request: ToolExecutionRequest):
        if False:
            yield {}


class _NoExceptionFailingExecutor(ToolExecutorPlugin):
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=False,
            error="opaque failure",
        )

    async def execute_stream(self, request: ToolExecutionRequest):
        if False:
            yield {}


class _DummyTool:
    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        return {"unused": True}


@pytest.mark.asyncio
async def test_bound_tool_invoke_returns_full_tool_execution_result():
    bound = _BoundTool(
        tool_id="t",
        tool=_DummyTool(),
        executor=_MetadataExecutor(),
    )
    result = await bound.invoke({}, context=object())
    assert isinstance(result, ToolExecutionResult)
    assert result.success is True
    assert result.data == {"value": 42}
    assert result.metadata["retry_attempts"] == 3
    assert result.metadata["timeout_ms"] == 1000


@pytest.mark.asyncio
async def test_bound_tool_invoke_failure_raises_exception():
    bound = _BoundTool(
        tool_id="t",
        tool=_DummyTool(),
        executor=_FailingExecutor(),
    )
    with pytest.raises(ToolError, match="boom"):
        await bound.invoke({}, context=object())


@pytest.mark.asyncio
async def test_bound_tool_invoke_failure_without_exception_raises_runtime_error():
    bound = _BoundTool(
        tool_id="t",
        tool=_DummyTool(),
        executor=_NoExceptionFailingExecutor(),
    )
    with pytest.raises(RuntimeError, match="opaque failure"):
        await bound.invoke({}, context=object())


@pytest.mark.asyncio
async def test_bound_tool_invoke_increments_usage_tool_calls():
    from openagents.interfaces.runtime import RunUsage

    class _Ctx:
        def __init__(self) -> None:
            self.run_request = None
            self.usage = RunUsage()
            self.agent_id = "a"
            self.session_id = "s"

    ctx = _Ctx()
    bound = _BoundTool(
        tool_id="t",
        tool=_DummyTool(),
        executor=_MetadataExecutor(),
    )
    await bound.invoke({}, context=ctx)
    assert ctx.usage.tool_calls == 1
