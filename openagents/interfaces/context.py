"""Context assembly contracts for runtime turn preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plugin import BasePlugin


@dataclass
class ContextAssemblyResult:
    """Prepared context for a single run."""

    transcript: list[dict[str, Any]] = field(default_factory=list)
    session_artifacts: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextAssemblerPlugin(BasePlugin):
    """Optional base class for context assembly hooks."""

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        """Build transcript/artifact context for a run."""
        return ContextAssemblyResult()

    async def compact(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> None:
        """Compact session transcript/artifacts when they grow too large.

        Called by the runtime before assemble() when the session
        exceeds configured thresholds. Implementations may summarize,
        prune, or archive older entries through the session_manager.
        """
        pass

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        """Finalize context bookkeeping after a run."""
        return result
