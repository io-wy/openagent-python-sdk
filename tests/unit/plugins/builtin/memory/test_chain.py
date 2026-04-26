"""WP3 stress: ChainMemory contract - inject in declared order, writeback in reverse."""

from __future__ import annotations

from typing import Any

import pytest

from openagents.decorators import memory
from openagents.interfaces.memory import MemoryPlugin
from openagents.plugins.builtin.memory.chain import ChainMemory

_LOG: list[str] = []


@memory("orderspy_a")
class OrderSpyMemoryA(MemoryPlugin):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
        )

    async def inject(self, context: Any) -> None:
        _LOG.append("inject:a")

    async def writeback(self, context: Any) -> None:
        _LOG.append("writeback:a")


@memory("orderspy_b")
class OrderSpyMemoryB(MemoryPlugin):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
        )

    async def inject(self, context: Any) -> None:
        _LOG.append("inject:b")

    async def writeback(self, context: Any) -> None:
        _LOG.append("writeback:b")


@memory("orderspy_c")
class OrderSpyMemoryC(MemoryPlugin):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
        )

    async def inject(self, context: Any) -> None:
        _LOG.append("inject:c")

    async def writeback(self, context: Any) -> None:
        _LOG.append("writeback:c")


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_chain_inject_in_order_writeback_in_reverse():
    _LOG.clear()
    chain = ChainMemory(
        config={
            "memories": [
                {"type": "orderspy_a"},
                {"type": "orderspy_b"},
                {"type": "orderspy_c"},
            ]
        }
    )

    class _Ctx:
        pass

    ctx = _Ctx()
    await chain.inject(ctx)
    await chain.writeback(ctx)
    assert _LOG == [
        "inject:a",
        "inject:b",
        "inject:c",
        "writeback:c",
        "writeback:b",
        "writeback:a",
    ]
