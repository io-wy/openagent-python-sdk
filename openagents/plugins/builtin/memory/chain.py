"""Chain memory - combines multiple memory plugins."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.memory import MemoryPlugin


class ChainMemory(MemoryPlugin):
    """Chain multiple memory plugins together.

    What:
        Composes child memories so ``inject`` calls each child in
        declared order and ``writeback`` calls them in reverse, the
        same lifecycle ordering used by middleware-style stacks.
        ``retrieve`` flattens hits from every child that implements
        the method.

    Usage:
        ``{"type": "chain", "config": {"memories": [{"type":
        "window_buffer", "config": {"window_size": 10}}, {"type":
        "buffer"}]}}``

    Depends on:
        - :func:`openagents.plugins.loader.load_plugin` (loads each
          declared child memory)
        - the configured child memories
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._memories: list[Any] = []
        self._load_memories()

    def _load_memories(self) -> None:
        """Load and instantiate memories from config."""
        from openagents.config.schema import MemoryRef
        from openagents.plugins.loader import load_plugin

        memories_config = self.config.get("memories", [])
        if not memories_config:
            raise ValueError("ChainMemory requires 'memories' config list")

        for i, mem_config in enumerate(memories_config):
            ref = MemoryRef.model_validate(mem_config)
            memory = load_plugin("memory", ref)
            self._memories.append(memory)

    async def inject(self, context: Any) -> None:
        """Call inject on each memory in order."""
        for memory in self._memories:
            if callable(getattr(memory, "inject", None)):
                await memory.inject(context)

    async def writeback(self, context: Any) -> None:
        """Call writeback on each memory in reverse order."""
        for memory in reversed(self._memories):
            if callable(getattr(memory, "writeback", None)):
                await memory.writeback(context)

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        """Merge results from all memories that implement retrieve."""
        results = []
        for memory in self._memories:
            if callable(getattr(memory, "retrieve", None)):
                mem_results = await memory.retrieve(query, context)
                if mem_results:
                    results.extend(mem_results)
        return results

    async def compact(self, context: Any) -> None:
        """Call compact on each memory in reverse order."""
        for memory in reversed(self._memories):
            if callable(getattr(memory, "compact", None)):
                await memory.compact(context)

    async def close(self) -> None:
        """Close all memories in reverse order."""
        for memory in reversed(self._memories):
            if hasattr(memory, "close"):
                await memory.close()
