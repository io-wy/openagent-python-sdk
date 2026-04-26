"""Common builtin tools."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.tool import ToolPlugin


class BuiltinSearchTool(ToolPlugin):
    """Search a tiny built-in document corpus by keyword score.

    What:
        Stub demo tool: scores three hard-coded snippets against the
        whitespace-tokenized query and returns the top matches.
        Useful for examples that need a "search" tool without
        external dependencies.

    Usage:
        ``{"id": "search", "type": "builtin_search"}``; invoke with
        ``{"query": "memory", "limit": 3}``.

    Depends on:
        - nothing (corpus is in-process).
    """

    name = "builtin_search"
    description = "Search the built-in knowledge corpus for relevant documents."

    _CORPUS = [
        {
            "title": "Agent Memory Design",
            "snippet": "Memory injection and writeback strategy for agent runtimes.",
        },
        {
            "title": "ReAct Pattern Notes",
            "snippet": "Pattern loops with tool calling and reasoning steps.",
        },
        {
            "title": "Session Concurrency Guide",
            "snippet": "Same session serial, cross session concurrent orchestration.",
        },
    ]

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        payload = params or {}
        query = str(payload.get("query", "")).strip()
        limit = payload.get("limit", 3)
        if not isinstance(limit, int) or limit <= 0:
            limit = 3

        words = [w.lower() for w in query.split() if w]
        if words:
            scored = []
            for row in self._CORPUS:
                text = f"{row['title']} {row['snippet']}".lower()
                score = sum(1 for w in words if w in text)
                scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            items = [row for score, row in scored if score > 0]
        else:
            items = list(self._CORPUS)

        return {"query": query, "items": items[:limit]}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query keywords"},
                "limit": {"type": "integer", "description": "Max results to return"},
            },
            "required": ["query"],
        }
