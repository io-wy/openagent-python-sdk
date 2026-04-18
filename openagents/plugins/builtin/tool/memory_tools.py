"""Memory-related tool plugins."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolPlugin


class RememberPreferenceTool(ToolPlugin):
    """Queue a preference for the paired MarkdownMemory to persist on writeback.

    What:
        Pushes ``{category, rule, reason}`` onto
        ``context.state['_pending_memory_writes']``. The companion
        ``MarkdownMemory`` plugin drains this list during writeback and
        appends each entry to the appropriate section file.
    Usage:
        ``{"id": "remember", "type": "remember_preference"}``; invoke with
        ``{"category": "user_feedback", "rule": "...", "reason": "..."}``.
    Depends on:
        Must be paired with ``markdown_memory`` in the agent's memory chain.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        category = str(params.get("category") or "user_feedback")
        rule = str(params.get("rule") or "").strip()
        reason = str(params.get("reason") or "").strip()
        if not rule:
            raise ValueError("'rule' is required")
        pending = context.state.setdefault("_pending_memory_writes", [])
        pending.append({"category": category, "rule": rule, "reason": reason})
        return {"queued": True, "count": len(pending)}
