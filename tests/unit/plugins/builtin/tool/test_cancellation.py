"""Tests for cancel_event-driven tool cancellation in SafeToolExecutor."""

from __future__ import annotations

import asyncio

import pytest

from openagents.errors.exceptions import ToolCancelledError, ToolTimeoutError
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionSpec,
    ToolPlugin,
)
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _SleepyTool(ToolPlugin):
    def __init__(self, sleep_s: float = 0.5):
        super().__init__(config={}, capabilities=set())
        self._sleep_s = sleep_s

    async def invoke(self, params, context):
        await asyncio.sleep(self._sleep_s)
        return "done"


class _BlockingTool(ToolPlugin):
    """Ignores cancel; sleeps for full duration via asyncio.shield."""

    def __init__(self, sleep_s: float = 0.2):
        super().__init__(config={}, capabilities=set())
        self._sleep_s = sleep_s

    async def invoke(self, params, context):
        await asyncio.shield(asyncio.sleep(self._sleep_s))
        return "finished"


def test_cancel_event_fires_before_completion_returns_cancelled_error():
    async def run():
        tool = _SleepyTool(sleep_s=1.0)
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        ev = asyncio.Event()
        req = ToolExecutionRequest(tool_id="sleepy", tool=tool, cancel_event=ev)

        async def fire():
            await asyncio.sleep(0.05)
            ev.set()

        asyncio.create_task(fire())
        result = await executor.execute(req)
        assert result.success is False
        assert isinstance(result.exception, ToolCancelledError)

    asyncio.run(run())


def test_timeout_still_wins_if_faster_than_cancel_event():
    async def run():
        tool = _SleepyTool(sleep_s=1.0)
        executor = SafeToolExecutor(config={"default_timeout_ms": 50})
        ev = asyncio.Event()  # never set
        req = ToolExecutionRequest(tool_id="sleepy", tool=tool, cancel_event=ev)
        result = await executor.execute(req)
        assert result.success is False
        assert isinstance(result.exception, ToolTimeoutError)

    asyncio.run(run())


def test_no_cancel_event_behaves_as_before():
    async def run():
        tool = _SleepyTool(sleep_s=0.02)
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        req = ToolExecutionRequest(tool_id="sleepy", tool=tool)  # cancel_event=None
        result = await executor.execute(req)
        assert result.success is True
        assert result.data == "done"

    asyncio.run(run())


def test_interrupt_behavior_block_waits_for_natural_completion():
    async def run():
        tool = _BlockingTool(sleep_s=0.2)
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        ev = asyncio.Event()
        spec = ToolExecutionSpec(interrupt_behavior="block")
        req = ToolExecutionRequest(
            tool_id="blocking", tool=tool, execution_spec=spec, cancel_event=ev
        )

        async def fire():
            await asyncio.sleep(0.05)
            ev.set()

        asyncio.create_task(fire())
        result = await executor.execute(req)
        # "block" means we wait; tool returns naturally.
        assert result.success is True
        assert result.data == "finished"

    asyncio.run(run())


def test_cancel_event_is_injected_into_bound_tool_request():
    """When ctx.scratch['__cancel_event__'] is populated, _BoundTool.invoke must
    thread it into the ToolExecutionRequest so SafeToolExecutor can race on it."""
    from openagents.plugins.builtin.runtime.default_runtime import _BoundTool
    from openagents.interfaces.tool import ToolExecutionResult

    class _CapturingExecutor:
        def __init__(self):
            self.captured: ToolExecutionRequest | None = None

        async def execute(self, request):
            self.captured = request
            return ToolExecutionResult(tool_id=request.tool_id, success=True, data=None)

        async def execute_stream(self, request):
            yield {"type": "result"}

        async def execute_batch(self, reqs):
            return [await self.execute(r) for r in reqs]

    class _NoopTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())

        async def invoke(self, params, context):
            return None

    async def run():
        tool = _NoopTool()
        executor = _CapturingExecutor()
        bound = _BoundTool(tool_id="n", tool=tool, executor=executor)

        class _CtxWithEvent:
            def __init__(self):
                self.scratch = {"__cancel_event__": asyncio.Event()}
                self.run_request = None
                self.usage = None
                self.agent_id = None
                self.session_id = None
                self.event_bus = None

        ctx = _CtxWithEvent()
        await bound.invoke({}, ctx)
        assert executor.captured is not None
        assert executor.captured.cancel_event is ctx.scratch["__cancel_event__"]

    asyncio.run(run())


def test_tool_raises_mid_execution_is_wrapped_as_tool_error():
    """When the tool raises mid-execution, the outer except wraps in ToolError
    even with cancel_event armed (but not set)."""
    from openagents.errors.exceptions import ToolError

    class _RaisingTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())

        async def invoke(self, params, context):
            await asyncio.sleep(0.01)
            raise ValueError("tool-internal boom")

    async def run():
        tool = _RaisingTool()
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        ev = asyncio.Event()  # armed but never set
        req = ToolExecutionRequest(tool_id="raiser", tool=tool, cancel_event=ev)
        result = await executor.execute(req)
        assert result.success is False
        assert isinstance(result.exception, ToolError)
        assert "boom" in (result.error or "")

    asyncio.run(run())
