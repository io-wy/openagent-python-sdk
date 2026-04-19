"""Tests for ToolPlugin.before_invoke / after_invoke hooks driven by _BoundTool."""

from __future__ import annotations

import asyncio

import pytest

from openagents.interfaces.tool import ToolPlugin
from openagents.plugins.builtin.runtime.default_runtime import _BoundTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _RecordingTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={}, capabilities=set())
        self.trace: list[str] = []
        self.last_after_args: tuple | None = None

    async def before_invoke(self, params, context):
        self.trace.append(f"before:{params}")

    async def invoke(self, params, context):
        self.trace.append(f"invoke:{params}")
        if params.get("fail"):
            raise RuntimeError("boom")
        return {"ok": True}

    async def after_invoke(self, params, context, result, exception=None):
        self.trace.append(f"after:{result}:{type(exception).__name__ if exception else None}")
        self.last_after_args = (params, result, exception)


class _Ctx:
    def __init__(self):
        self.scratch: dict = {}
        self.run_request = None
        self.usage = None
        self.agent_id = None
        self.session_id = None
        self.event_bus = None


def test_before_and_after_invoke_both_called_on_success():
    async def run():
        tool = _RecordingTool()
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        bound = _BoundTool(tool_id="rec", tool=tool, executor=executor)
        ctx = _Ctx()
        result = await bound.invoke({"x": 1}, ctx)
        assert result.success is True
        assert any(s.startswith("before:") for s in tool.trace)
        assert any(s.startswith("invoke:") for s in tool.trace)
        assert any(s.startswith("after:") for s in tool.trace)

    asyncio.run(run())


def test_after_invoke_called_on_failure_with_exception_set():
    async def run():
        tool = _RecordingTool()
        executor = SafeToolExecutor(config={"default_timeout_ms": 5000})
        bound = _BoundTool(tool_id="rec", tool=tool, executor=executor)
        ctx = _Ctx()
        with pytest.raises(Exception):
            await bound.invoke({"fail": True}, ctx)
        assert tool.last_after_args is not None
        _, _, exc = tool.last_after_args
        assert exc is not None
