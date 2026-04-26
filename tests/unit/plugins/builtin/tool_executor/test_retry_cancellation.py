"""WP3 stress: cancelling RetryToolExecutor during backoff propagates CancelledError."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openagents.errors.exceptions import RetryableToolError
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor


class _AlwaysFailExecutor(ToolExecutorPlugin):
    """Inner executor that always returns a retryable failure."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=False,
            error="boom",
            exception=RetryableToolError("boom", request.tool_id),
        )

    async def execute_stream(self, request: ToolExecutionRequest):
        yield {"type": "result", "data": None, "error": "boom"}


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_cancel_during_backoff_raises_cancelled_error():
    """The retry loop sleeps between attempts; cancel must propagate."""
    inner = _AlwaysFailExecutor()
    executor = RetryToolExecutor(
        config={
            "inner": {"impl": "tests.unit.plugins.builtin.tool_executor.test_retry_cancellation._AlwaysFailExecutor"},
            "max_attempts": 5,
            "initial_delay_ms": 200,
            "backoff_multiplier": 2.0,
            "max_delay_ms": 2000,
        }
    )
    # Replace with our real instance so we don't need impl-loading magic.
    executor._inner = inner

    class _FakeTool:
        async def invoke(self, params, ctx):
            raise RetryableToolError("boom", "fake")

        async def invoke_stream(self, params, ctx):
            yield None

    request = ToolExecutionRequest(
        tool_id="fake",
        tool=_FakeTool(),
        params={},
        context=object(),
    )

    task = asyncio.create_task(executor.execute(request))
    # Let the first inner call return and then a sleep begin.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
