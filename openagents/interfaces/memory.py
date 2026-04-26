"""Memory plugin contract.

Two ways to implement memory:

1. **Protocol (recommended -- no inheritance needed)**::

       class MyMemory:
           config = {}

           async def inject(self, context):
               context.memory_view["history"] = [...]

           async def writeback(self, context):
               ...

           async def retrieve(self, query, context):
               return [...]

2. **BasePlugin (optional)**::

       from openagents.interfaces.memory import MemoryPlugin

       class MyMemory(MemoryPlugin):
           async def inject(self, context): ...
           async def writeback(self, context): ...
           async def retrieve(self, query, context): ...

3. **Decorator (easiest)**::

       from openagents import memory

       @memory
       class MyMemory:
           async def inject(self, context): ...
           async def writeback(self, context): ...
           async def retrieve(self, query, context): ...
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .plugin import BasePlugin


@runtime_checkable
class Memory(Protocol):
    """Protocol for memory plugins.

    Required: config
    Optional: inject, writeback, retrieve, compact, close
    """

    @property
    def config(self) -> dict[str, Any]: ...

    async def inject(self, context: Any) -> None: ...

    async def writeback(self, context: Any) -> None: ...

    async def compact(self, context: Any) -> None: ...


class MemoryPlugin(BasePlugin):
    """Base memory plugin (optional base class).

    You don't have to inherit from this!
    Use it for convenience, or implement the Memory Protocol directly.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        self.config: dict[str, Any] = config or {}

    async def inject(self, context: Any) -> None:
        pass

    async def writeback(self, context: Any) -> None:
        pass

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        return []

    async def compact(self, context: Any) -> None:
        pass

    async def close(self) -> None:
        pass
