"""Batch-aware tool executor.

Partitions a batch of requests by ``execution_spec.concurrency_safe`` and runs the
safe group in parallel (bounded by a semaphore) and the unsafe group sequentially,
while preserving result order.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class ConcurrentBatchExecutor(TypedConfigPluginMixin, ToolExecutorPlugin):
    """Executor that parallelizes ``concurrency_safe`` tools in a batch.

    What:
        ``execute(req)`` delegates to the configured inner executor.
        ``execute_batch(reqs)`` partitions on ``req.execution_spec.concurrency_safe``:
          - safe group   -> ``asyncio.gather`` with a ``Semaphore(max_concurrency)``
          - unsafe group -> sequential in input order
        Results are returned in the same order as the input ``reqs``.

    Usage:
        ``{"tool_executor": {"type": "concurrent_batch",
            "config": {"inner": {"type": "safe"}, "max_concurrency": 10}}}``

    Depends on:
        - The inner executor loaded via ``openagents.plugins.loader.load_plugin``.
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_concurrency: int = 10

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        self._init_typed_config()
        self._max_concurrency = max(1, int(self.cfg.max_concurrency))
        self._inner = self._load_inner(self.cfg.inner)

    def _load_inner(self, ref: dict[str, Any]):
        from openagents.config.schema import ToolExecutorRef
        from openagents.plugins.loader import load_plugin

        return load_plugin(
            "tool_executor",
            ToolExecutorRef(**ref),
            required_methods=("execute", "execute_stream"),
        )

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return await self._inner.execute(request)

    async def execute_stream(self, request: ToolExecutionRequest):
        async for chunk in self._inner.execute_stream(request):
            yield chunk

    async def execute_batch(
        self,
        requests: list[ToolExecutionRequest],
    ) -> list[ToolExecutionResult]:
        if not requests:
            return []

        results: list[ToolExecutionResult | None] = [None] * len(requests)
        sem = asyncio.Semaphore(self._max_concurrency)

        safe_indices: list[int] = []
        unsafe_indices: list[int] = []
        for idx, req in enumerate(requests):
            if bool(req.execution_spec.concurrency_safe):
                safe_indices.append(idx)
            else:
                unsafe_indices.append(idx)

        async def run_one(idx: int) -> None:
            async with sem:
                results[idx] = await self._inner.execute(requests[idx])

        # Parallel safe group.
        if safe_indices:
            await asyncio.gather(*(run_one(i) for i in safe_indices))

        # Sequential unsafe group (preserves input order within the group).
        for idx in unsafe_indices:
            results[idx] = await self._inner.execute(requests[idx])

        # Every slot must be filled by construction.
        return [r for r in results if r is not None]
