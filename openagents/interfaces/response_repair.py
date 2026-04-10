"""Response repair contracts for provider/runtime recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plugin import BasePlugin


@dataclass
class ResponseRepairDecision:
    """Structured decision for repairing a bad or empty model response."""

    handled: bool = False
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ResponseRepairPolicyPlugin(BasePlugin):
    """Optional base class for post-model response repair."""

    async def repair_empty_response(
        self,
        *,
        context: Any,
        messages: list[dict[str, Any]],
        assistant_content: list[dict[str, Any]],
        stop_reason: str | None,
        retries: int,
    ) -> ResponseRepairDecision | None:
        """Repair an empty response after model retries are exhausted."""
        return None
