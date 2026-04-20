# tests/unit/test_tavily_search_tool.py
from __future__ import annotations

import pytest
import respx
from httpx import Response

from openagents.plugins.builtin.tool.tavily_search import TavilySearchTool


@pytest.mark.asyncio
@respx.mock
async def test_basic_search(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "secret")
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=Response(
            200,
            json={
                "query": "openagents",
                "results": [
                    {"url": "https://x.example", "title": "X", "content": "snippet", "score": 0.9},
                ],
            },
        )
    )
    tool = TavilySearchTool(config={})
    result = await tool.invoke({"query": "openagents"}, context=None)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "openagents" in body
    assert "secret" in body
    assert result["query"] == "openagents"
    assert result["results"][0]["url"] == "https://x.example"


@pytest.mark.asyncio
async def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    tool = TavilySearchTool(config={})
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await tool.invoke({"query": "x"}, context=None)


@pytest.mark.asyncio
@respx.mock
async def test_domain_filters_forwarded(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=Response(200, json={"query": "q", "results": []})
    )
    tool = TavilySearchTool(config={})
    await tool.invoke(
        {
            "query": "q",
            "include_domains": ["example.com"],
            "exclude_domains": ["bad.com"],
            "max_results": 7,
            "search_depth": "advanced",
        },
        context=None,
    )
    payload = route.calls.last.request.content.decode()
    assert "example.com" in payload
    assert "bad.com" in payload
    assert '"max_results":7' in payload.replace(" ", "")
    assert "advanced" in payload
