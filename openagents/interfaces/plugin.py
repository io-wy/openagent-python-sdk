"""Base plugin contracts.

Two approaches for custom plugins:

1. **Protocol (recommended)** -- no inheritance needed::

       class MyTool:
           async def invoke(self, params: dict, context: Any) -> Any:
               ...

2. **BasePlugin (optional convenience)**::

       class MyTool(BasePlugin):
           async def invoke(self, params: dict, context: Any) -> Any:
               ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Protocol that all plugins must follow.

    Any class with a ``config`` dict can be a plugin.
    No inheritance required!
    """

    @property
    def config(self) -> dict[str, Any]: ...


@dataclass
class BasePlugin:
    """Convenience base plugin with config helper.

    Optional -- you don't have to inherit from this.
    """

    config: dict[str, Any] = field(default_factory=dict)
