"""Base plugin contracts.

This module provides both:
1. Protocol definitions - for duck typing (recommended for custom plugins)
2. BasePlugin - convenience base class (optional, for懒人)

Example using Protocol (recommended):
    class MyTool:
        async def invoke(self, params: dict, context: Any) -> Any:
            ...

    # Loader will validate at runtime

Example using BasePlugin (optional):
    class MyTool(BasePlugin):
        def __init__(self, config=None):
            super().__init__(config=config, capabilities={"tool.invoke"})

        async def invoke(self, params: dict, context: Any) -> Any:
            ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

from .capabilities import normalize_capabilities


@runtime_checkable
class Plugin(Protocol):
    """Protocol that all plugins must follow.

    Any class with config and capabilities can be a plugin.
    No inheritance required!
    """

    @property
    def config(self) -> dict[str, Any]: ...

    @property
    def capabilities(self) -> set[str]: ...


@dataclass
class BasePlugin:
    """Convenience base plugin with config and capability helpers.

    Optional - you don't have to inherit from this.
    Use it for convenience, or implement the Protocol directly.
    """

    config: dict[str, Any] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)

    def capability_set(self) -> set[str]:
        return normalize_capabilities(self.capabilities)

    def supports(self, capability: str) -> bool:
        return capability in self.capability_set()

    @classmethod
    def from_capabilities(
        cls,
        *,
        config: dict[str, Any] | None = None,
        capabilities: Iterable[str] | None = None,
    ) -> "BasePlugin":
        return cls(
            config=config or {},
            capabilities=normalize_capabilities(capabilities),
        )
