"""Tests for _BoundTool background job routing (invoke_background / poll_job / cancel_job)."""

from __future__ import annotations

import asyncio
import time

import pytest

from openagents.interfaces.tool import JobHandle, JobStatus, ToolPlugin
from openagents.plugins.builtin.runtime.default_runtime import _BoundTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _BgTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={}, capabilities=set())
        self._next = 0
        self._jobs: dict[str, JobStatus] = {}

    async def invoke(self, params, context):
        raise NotImplementedError("use invoke_background")

    async def invoke_background(self, params, context):
        self._next += 1
        job_id = f"job-{self._next}"
        self._jobs[job_id] = JobStatus(job_id=job_id, status="running", progress=0.0)
        return JobHandle(job_id=job_id, tool_id="bg", status="running", created_at=time.time())

    async def poll_job(self, handle, context):
        return self._jobs[handle.job_id]

    async def cancel_job(self, handle, context):
        if handle.job_id in self._jobs:
            self._jobs[handle.job_id] = JobStatus(job_id=handle.job_id, status="cancelled")
            return True
        return False


def test_invoke_background_returns_handle():
    async def run():
        tool = _BgTool()
        bound = _BoundTool(tool_id="bg", tool=tool, executor=SafeToolExecutor())
        handle = await bound.invoke_background({}, context=None)
        assert isinstance(handle, JobHandle)
        assert handle.status == "running"

    asyncio.run(run())


def test_poll_job_returns_status():
    async def run():
        tool = _BgTool()
        bound = _BoundTool(tool_id="bg", tool=tool, executor=SafeToolExecutor())
        handle = await bound.invoke_background({}, context=None)
        status = await bound.poll_job(handle, context=None)
        assert isinstance(status, JobStatus)
        assert status.job_id == handle.job_id

    asyncio.run(run())


def test_cancel_job_returns_true_and_updates_status():
    async def run():
        tool = _BgTool()
        bound = _BoundTool(tool_id="bg", tool=tool, executor=SafeToolExecutor())
        handle = await bound.invoke_background({}, context=None)
        ok = await bound.cancel_job(handle, context=None)
        assert ok is True
        status = await bound.poll_job(handle, context=None)
        assert status.status == "cancelled"

    asyncio.run(run())


def test_invoke_background_unsupported_tool_raises():
    class _NoBg(ToolPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())

        async def invoke(self, params, context):
            return "ok"

    async def run():
        tool = _NoBg()
        bound = _BoundTool(tool_id="nobg", tool=tool, executor=SafeToolExecutor())
        with pytest.raises(NotImplementedError):
            await bound.invoke_background({}, context=None)

    asyncio.run(run())
