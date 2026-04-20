"""Tests for ConcurrentBatchExecutor — partition-by-concurrency_safe + Semaphore limits."""

from __future__ import annotations

import asyncio
import time

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


def test_pattern_call_tool_batch_groups_by_tool_id_and_preserves_order():
    from openagents.interfaces.pattern import PatternPlugin
    from openagents.interfaces.run_context import RunContext
    from openagents.plugins.builtin.runtime.default_runtime import _BoundTool

    class _StubEventBus:
        def __init__(self):
            self.emitted: list[tuple[str, dict]] = []

        async def emit(self, name, **payload):
            self.emitted.append((name, payload))

    async def run():
        tool_a = _SleepTool(concurrency_safe=True, sleep_s=0.01)
        tool_b = _SleepTool(concurrency_safe=True, sleep_s=0.01)
        executor = ConcurrentBatchExecutor(config={})
        bound_a = _BoundTool(tool_id="a", tool=tool_a, executor=executor)
        bound_b = _BoundTool(tool_id="b", tool=tool_b, executor=executor)

        pattern = PatternPlugin()
        event_bus = _StubEventBus()
        pattern.context = RunContext(
            agent_id="ag",
            session_id="se",
            run_id="r",
            input_text="",
            event_bus=event_bus,
            tools={"a": bound_a, "b": bound_b},
        )
        results = await pattern.call_tool_batch(
            [
                ("a", {"i": 1}),
                ("b", {"i": 2}),
                ("a", {"i": 3}),
            ]
        )
        assert results == [1, 2, 3]
        names = [n for n, _ in event_bus.emitted]
        assert "tool.batch.started" in names
        assert "tool.batch.completed" in names

    asyncio.run(run())


def test_bound_tool_invoke_batch_preserves_order_and_item_ids():
    from openagents.interfaces.tool import BatchItem, BatchResult
    from openagents.plugins.builtin.runtime.default_runtime import _BoundTool

    async def run():
        tool = _SleepTool(concurrency_safe=True, sleep_s=0.01)
        executor = ConcurrentBatchExecutor(config={})
        bound = _BoundTool(tool_id="sleep", tool=tool, executor=executor)
        items = [BatchItem(params={"i": i}) for i in range(4)]
        results = await bound.invoke_batch(items, context=None)
        assert isinstance(results, list)
        assert len(results) == 4
        for item, r in zip(items, results):
            assert isinstance(r, BatchResult)
            assert r.item_id == item.item_id
            assert r.success is True
        assert [r.data for r in results] == [0, 1, 2, 3]

    asyncio.run(run())


def test_concurrent_batch_defends_against_raising_inner_executor():
    """If a (misbehaving) inner executor raises, we wrap per-request — the batch survives."""
    from openagents.errors.exceptions import ToolError
    from openagents.interfaces.tool import ToolExecutorPlugin

    class _RaisingInner(ToolExecutorPlugin):
        def __init__(self):
            super().__init__(config={}, capabilities=set())
            self.calls = 0

        async def execute(self, request):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("inner misbehavior")
            return ToolExecutionResult(tool_id=request.tool_id, success=True, data=self.calls)

        async def execute_stream(self, request):
            yield {"type": "result"}

    async def run():
        tool = _SleepTool(concurrency_safe=False, sleep_s=0.0)
        executor = ConcurrentBatchExecutor(config={})
        # Replace inner with a raising one directly (bypasses _load_inner).
        executor._inner = _RaisingInner()
        reqs = [_mk_req(tool, i, safe=False) for i in range(3)]
        results = await executor.execute_batch(reqs)
        assert len(results) == 3
        # The middle request had inner raise; it must be reported as failure not crashed batch.
        assert results[1].success is False
        assert isinstance(results[1].exception, ToolError)
        assert results[0].success is True
        assert results[2].success is True

    asyncio.run(run())
