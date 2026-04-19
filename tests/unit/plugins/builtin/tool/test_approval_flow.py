"""Tests for the _BoundTool approval gate driven by ToolPlugin.requires_approval."""

from __future__ import annotations

import asyncio

import pytest

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin
from openagents.plugins.builtin.runtime.default_runtime import _BoundTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _NeedsApprovalTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={}, capabilities=set())

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(approval_mode="always")

    async def invoke(self, params, context):
        return "executed"


class _StubRunRequest:
    def __init__(self, approvals: dict[str, str] | None = None):
        self.budget = None
        self.context_hints = {"approvals": approvals or {}}
        self.run_id = "run1"


class _Ctx:
    def __init__(self, approvals=None):
        self.scratch: dict = {}
        self.run_request = _StubRunRequest(approvals=approvals)
        self.usage = None
        self.agent_id = "a"
        self.session_id = "s"
        self.event_bus = None


def test_approval_required_but_missing_raises():
    async def run():
        tool = _NeedsApprovalTool()
        bound = _BoundTool(tool_id="risky", tool=tool, executor=SafeToolExecutor())
        ctx = _Ctx(approvals={})
        with pytest.raises(PermanentToolError, match="approval"):
            await bound.invoke({}, ctx)

    asyncio.run(run())


def test_approval_allow_proceeds():
    async def run():
        tool = _NeedsApprovalTool()
        bound = _BoundTool(tool_id="risky", tool=tool, executor=SafeToolExecutor())
        ctx = _Ctx(approvals={"*": "allow"})
        result = await bound.invoke({}, ctx)
        assert result.success is True
        assert result.data == "executed"

    asyncio.run(run())


def test_approval_deny_raises():
    async def run():
        tool = _NeedsApprovalTool()
        bound = _BoundTool(tool_id="risky", tool=tool, executor=SafeToolExecutor())
        ctx = _Ctx(approvals={"*": "deny"})
        with pytest.raises(PermanentToolError, match="denied"):
            await bound.invoke({}, ctx)

    asyncio.run(run())
