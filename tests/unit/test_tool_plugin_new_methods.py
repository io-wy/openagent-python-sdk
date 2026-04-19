"""Tests for new ToolPlugin models and methods (batch / background / hooks / approval)."""

from __future__ import annotations

import pytest

from openagents.interfaces.tool import (
    BatchItem,
    BatchResult,
    JobHandle,
    JobStatus,
    ToolExecutionRequest,
)


def test_batch_item_auto_generates_item_id():
    item = BatchItem(params={"x": 1})
    assert item.item_id
    assert item.params == {"x": 1}


def test_batch_result_preserves_item_id():
    r = BatchResult(item_id="abc", success=True, data=42)
    assert r.item_id == "abc"
    assert r.success is True
    assert r.data == 42


def test_job_handle_requires_status():
    h = JobHandle(job_id="j1", tool_id="t", status="pending", created_at=1.0)
    assert h.status == "pending"
    with pytest.raises(Exception):
        JobHandle(job_id="j1", tool_id="t", status="bogus", created_at=1.0)


def test_job_status_optional_progress():
    s = JobStatus(job_id="j1", status="running")
    assert s.progress is None


def test_tool_execution_request_accepts_cancel_event():
    import asyncio
    ev = asyncio.Event()
    req = ToolExecutionRequest(tool_id="t", tool=None, cancel_event=ev)
    assert req.cancel_event is ev


def test_tool_execution_request_cancel_event_defaults_none():
    req = ToolExecutionRequest(tool_id="t", tool=None)
    assert req.cancel_event is None


import asyncio

from openagents.interfaces.tool import ToolPlugin, ToolExecutionSpec


class _DummyTool(ToolPlugin):
    def __init__(self, spec: ToolExecutionSpec | None = None):
        super().__init__(config={}, capabilities=set())
        self._spec = spec or ToolExecutionSpec()
        self.invoked: list[dict] = []

    def execution_spec(self) -> ToolExecutionSpec:
        return self._spec

    async def invoke(self, params, context):
        self.invoked.append(params)
        return {"echoed": params}


def test_invoke_batch_default_runs_sequentially_and_preserves_order():
    tool = _DummyTool()
    items = [BatchItem(params={"n": i}) for i in range(3)]
    results = asyncio.run(tool.invoke_batch(items, context=None))
    assert [r.item_id for r in results] == [i.item_id for i in items]
    assert all(r.success for r in results)
    assert [r.data for r in results] == [{"echoed": {"n": 0}}, {"echoed": {"n": 1}}, {"echoed": {"n": 2}}]


def test_invoke_batch_default_captures_per_item_errors():
    class _Flaky(_DummyTool):
        async def invoke(self, params, context):
            if params.get("fail"):
                raise ValueError("boom")
            return "ok"

    tool = _Flaky()
    items = [BatchItem(params={}), BatchItem(params={"fail": True}), BatchItem(params={})]
    results = asyncio.run(tool.invoke_batch(items, context=None))
    assert [r.success for r in results] == [True, False, True]
    assert results[1].error == "boom" or "boom" in (results[1].error or "")


def test_invoke_background_default_raises_not_implemented():
    tool = _DummyTool()
    with pytest.raises(NotImplementedError):
        asyncio.run(tool.invoke_background({}, context=None))


def test_poll_and_cancel_job_default_raise_not_implemented():
    tool = _DummyTool()
    handle = JobHandle(job_id="j", tool_id="t", status="pending", created_at=0.0)
    with pytest.raises(NotImplementedError):
        asyncio.run(tool.poll_job(handle, context=None))
    with pytest.raises(NotImplementedError):
        asyncio.run(tool.cancel_job(handle, context=None))


def test_requires_approval_default_reads_execution_spec():
    always = _DummyTool(ToolExecutionSpec(approval_mode="always"))
    never = _DummyTool(ToolExecutionSpec(approval_mode="never"))
    inherit = _DummyTool(ToolExecutionSpec(approval_mode="inherit"))
    assert always.requires_approval({}, context=None) is True
    assert never.requires_approval({}, context=None) is False
    assert inherit.requires_approval({}, context=None) is False


def test_before_and_after_invoke_default_no_op():
    tool = _DummyTool()
    asyncio.run(tool.before_invoke({}, context=None))
    asyncio.run(tool.after_invoke({}, context=None, result={"ok": True}))


from openagents.interfaces.tool import ToolExecutorPlugin, ToolExecutionResult


def test_tool_executor_plugin_default_execute_batch_is_sequential():
    class _Recording(ToolExecutorPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())
            self.calls: list[str] = []

        async def execute(self, request):
            self.calls.append(request.tool_id)
            return ToolExecutionResult(tool_id=request.tool_id, success=True, data=request.tool_id)

    exec_plugin = _Recording()
    reqs = [ToolExecutionRequest(tool_id=f"t{i}", tool=None) for i in range(3)]
    results = asyncio.run(exec_plugin.execute_batch(reqs))
    assert [r.tool_id for r in results] == ["t0", "t1", "t2"]
    assert [r.success for r in results] == [True, True, True]
    assert exec_plugin.calls == ["t0", "t1", "t2"]
