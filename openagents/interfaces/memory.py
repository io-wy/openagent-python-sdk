"""Memory plugin contract.

Two ways to implement memory:

1. Protocol (recommended - no inheritance needed):
    class MyMemory:
        config = {}
        capabilities = {"memory.inject", "memory.writeback", "memory.retrieve"}

        async def inject(self, context):
            '''Inject memory into context'''
            context.memory_view["history"] = [...]

        async def writeback(self, context):
            '''Save current interaction'''
            ...

        async def retrieve(self, query, context):
            '''Search memory for relevant info'''
            return [...]

2. BasePlugin (optional):
    from openagents.interfaces.memory import MemoryPlugin

    class MyMemory(MemoryPlugin):
        def __init__(self, config=None):
            super().__init__(config=config, capabilities={"memory.inject", "memory.writeback", "memory.retrieve"})

        async def inject(self, context): ...
        async def writeback(self, context): ...
        async def retrieve(self, query, context): ...

3. Decorator (easiest):
    from openagents import memory

    @memory
    class MyMemory:
        async def inject(self, context):
            context.memory_view["history"] = [...]

        async def writeback(self, context):
            ...

        async def retrieve(self, query, context):
            '''Search memory for relevant info'''
            return [...]
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .capabilities import MEMORY_INJECT, MEMORY_WRITEBACK


@runtime_checkable
class Memory(Protocol):
    """Protocol for memory plugins.

    Required: config, capabilities
    Optional: inject, writeback, retrieve, close
    """

    @property
    def config(self) -> dict[str, Any]: ...

    @property
    def capabilities(self) -> set[str]: ...

    async def inject(self, context: Any) -> None: ...

    async def writeback(self, context: Any) -> None: ...

    async def compact(self, context: Any) -> None: ...


class MemoryPlugin:
    """Base memory plugin (optional base class).

    You don't have to inherit from this!
    Use it for convenience, or implement the Memory Protocol directly.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        capabilities: set[str] | None = None,
    ):
        self.config: dict[str, Any] = config or {}
        self.capabilities: set[str] = capabilities or {MEMORY_INJECT, MEMORY_WRITEBACK}

    async def inject(self, context: Any) -> None:
        """Inject memory into execution context.

        Called before pattern execution to provide context.
        Should set context.memory_view with relevant data.
        """
        pass

    async def writeback(self, context: Any) -> None:
        """Write memory updates from execution context.

        Called after pattern execution to save interaction.
        Can access context.input_text, context.tool_results, etc.
        """
        pass

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        """Search memory for relevant information.

        Called during execution to get relevant context.
        Should return list of relevant memory entries.

        Args:
            query: Search query (can be keywords, embedding, etc.)
            context: Execution context

        Returns:
            List of relevant memory entries
        """
        return []

    async def compact(self, context: Any) -> None:
        """Compact memory storage when it grows too large.

        Called after writeback when the runtime decides memory
        compaction is needed. Implementations should summarize,
        merge, or prune entries to reduce size.
        """
        pass

    async def close(self) -> None:
        """Cleanup resources."""
        pass
