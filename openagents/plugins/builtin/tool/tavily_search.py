"""Tavily search tool — REST-based web search (MCP fallback)."""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from openagents.interfaces.tool import ToolPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin

_API_URL = "https://api.tavily.com/search"


class TavilySearchTool(TypedConfigPluginMixin, ToolPlugin):
    """REST-based Tavily search tool (MCP fallback).

    What:
        POSTs to Tavily's REST ``/search`` endpoint with the configured API
        key. Used when the Tavily MCP server is unavailable.
    Usage:
        ``{"id": "tavily", "type": "tavily_search"}``; invoke with
        ``{"query": "...", "max_results": 5}``.
    Depends on:
        ``httpx.AsyncClient``; key read from ``TAVILY_API_KEY`` env.
    """

    class Config(BaseModel):
        api_key_env: str = "TAVILY_API_KEY"
        default_max_results: int = 5
        default_search_depth: Literal["basic", "advanced"] = "basic"
        timeout_ms: int = 15_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        query = str(params.get("query") or "").strip()
        if not query:
            raise ValueError("'query' is required")

        api_key = os.environ.get(self.cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.cfg.api_key_env} is not set; cannot call Tavily")

        payload: dict[str, Any] = {
            "api_key": api_key,
            "query": query,
            "max_results": int(params.get("max_results") or self.cfg.default_max_results),
            "search_depth": params.get("search_depth") or self.cfg.default_search_depth,
        }
        include = params.get("include_domains")
        exclude = params.get("exclude_domains")
        if include:
            payload["include_domains"] = list(include)
        if exclude:
            payload["exclude_domains"] = list(exclude)

        timeout = httpx.Timeout(self.cfg.timeout_ms / 1000.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_API_URL, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(f"Tavily API returned HTTP {exc.response.status_code}") from None
            data = resp.json()
        return {
            "query": data.get("query", query),
            "results": data.get("results", []),
            "search_depth": payload["search_depth"],
        }
