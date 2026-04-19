"""Tests for ConcurrentBatchExecutor — partition-by-concurrency_safe + Semaphore limits."""

from __future__ import annotations

import asyncio
import time

import pytest

from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
    ToolPlugin,
)
from openagents.plugins.builtin.tool_executor.concurrent_batch import ConcurrentBatchExecutor


class _SleepTool(ToolPlugin):
    def __init__(self, concurrency_safe: bool, sleep_s: float):
        super().__init__(config={}, capabilities=set())
        self._safe = concurrency_safe
        self._sleep_s = sleep_s

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=self._safe)

    async def invoke(self, params, context):
        await asyncio.sleep(self._sleep_s)
        return params.get("i")


def _mk_req(tool, i, safe=True):
    return ToolExecutionRequest(
        tool_id=tool.tool_name,
        tool=tool,
        params={"i": i},
        execution_spec=ToolExecutionSpec(concurrency_safe=safe),
    )


def test_concurrent_batch_runs_safe_in_parallel():
    async def run():
        tool = _SleepTool(concurrency_safe=True, sleep_s=0.1)
        reqs = [_mk_req(tool, i, safe=True) for i in range(5)]
        executor = ConcurrentBatchExecutor(config={"max_concurrency": 5})
        started = time.perf_counter()
        results = await executor.execute_batch(reqs)
        elapsed = time.perf_counter() - started
        assert [r.data for r in results] == [0, 1, 2, 3, 4]
        assert elapsed < 0.3, f"expected parallelism, took {elapsed:.2f}s"

    asyncio.run(run())


def test_concurrent_batch_runs_unsafe_in_series():
    async def run():
        tool = _SleepTool(concurrency_safe=False, sleep_s=0.1)
        reqs = [_mk_req(tool, i, safe=False) for i in range(3)]
        executor = ConcurrentBatchExecutor(config={})
        started = time.perf_counter()
        results = await executor.execute_batch(reqs)
        elapsed = time.perf_counter() - started
        assert [r.data for r in results] == [0, 1, 2]
        assert elapsed >= 0.25

    asyncio.run(run())


def test_concurrent_batch_preserves_order_when_mixed():
    async def run():
        fast = _SleepTool(concurrency_safe=True, sleep_s=0.05)
        slow = _SleepTool(concurrency_safe=False, sleep_s=0.05)
        reqs = [_mk_req(slow, 0, safe=False), _mk_req(fast, 1, safe=True), _mk_req(slow, 2, safe=False)]
        executor = ConcurrentBatchExecutor(config={})
        results = await executor.execute_batch(reqs)
        assert [r.data for r in results] == [0, 1, 2]

    asyncio.run(run())


def test_single_execute_delegates_to_inner():
    async def run():
        tool = _SleepTool(concurrency_safe=True, sleep_s=0.01)
        executor = ConcurrentBatchExecutor(config={})
        result = await executor.execute(_mk_req(tool, 42, safe=True))
        assert result.success is True
        assert result.data == 42

    asyncio.run(run())


def test_max_concurrency_bounds_parallelism():
    async def run():
        tool = _SleepTool(concurrency_safe=True, sleep_s=0.1)
        reqs = [_mk_req(tool, i, safe=True) for i in range(4)]
        executor = ConcurrentBatchExecutor(config={"max_concurrency": 2})
        started = time.perf_counter()
        await executor.execute_batch(reqs)
        elapsed = time.perf_counter() - started
        # With max 2 concurrent and 4 tasks × 0.1s, expect ~0.2s
        assert elapsed >= 0.18

    asyncio.run(run())
