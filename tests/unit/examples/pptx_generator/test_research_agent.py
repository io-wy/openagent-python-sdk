"""Tests for ResearchPattern (stage 3 of pptx-agent wizard).

Covers:
- Happy path: MCP tool returns results, LLM synthesizes findings
- Empty queries: short-circuit without calling LLM
- MCP failure: fallback to REST tool
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator.app.plugins import ResearchPattern
from examples.pptx_generator.state import ResearchFindings


def _make_ctx(*, intent=None, llm_return: str = ""):
    return SimpleNamespace(
        input_text="",
        state={"intent": intent or {}},
        memory_view={},
        tool_results=[],
        assembly_metadata={},
        llm_client=SimpleNamespace(complete=AsyncMock(return_value=llm_return)),
        tools={},
    )


@pytest.mark.asyncio
async def test_research_happy_path(monkeypatch):
    tool_data = {
        "query": "q1",
        "results": [
            {"url": "https://a", "title": "A", "content": "fact A", "score": 0.9},
        ],
    }
    findings_json = json.dumps({
        "queries_executed": ["q1"],
        "sources": [{"url": "https://a", "title": "A", "snippet": "fact A"}],
        "key_facts": ["A says fact A"],
        "caveats": [],
    })
    intent = {"research_queries": ["q1"]}
    ctx = _make_ctx(intent=intent, llm_return=findings_json)

    async def run_tool(tool_id, params):
        return SimpleNamespace(data=tool_data)

    pattern = ResearchPattern(config={})
    pattern.context = ctx
    pattern.call_tool = run_tool
    result = await pattern.execute()
    assert isinstance(result, ResearchFindings)
    assert result.key_facts == ["A says fact A"]
    assert ctx.state["research"]["key_facts"] == ["A says fact A"]


@pytest.mark.asyncio
async def test_research_empty_queries_returns_empty_findings():
    ctx = _make_ctx(intent={"research_queries": []})
    pattern = ResearchPattern(config={})
    pattern.context = ctx
    result = await pattern.execute()
    assert isinstance(result, ResearchFindings)
    assert result.sources == []
    # LLM not called at all
    ctx.llm_client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_research_falls_back_to_tavily_rest_when_mcp_errors():
    mcp_call_count = {"n": 0}
    rest_call_count = {"n": 0}

    async def run_tool(tool_id, params):
        if tool_id == "tavily_mcp":
            mcp_call_count["n"] += 1
            raise RuntimeError("MCP unavailable")
        if tool_id == "tavily_fallback":
            rest_call_count["n"] += 1
            return SimpleNamespace(data={"query": params["query"], "results": [{"url": "u", "title": "t", "content": "s"}]})
        raise AssertionError(f"unexpected tool {tool_id}")

    findings_json = json.dumps({
        "queries_executed": ["q"],
        "sources": [{"url": "u", "title": "t", "snippet": "s"}],
        "key_facts": ["f"],
        "caveats": [],
    })
    ctx = SimpleNamespace(
        input_text="",
        state={"intent": {"research_queries": ["q"]}},
        memory_view={},
        tool_results=[],
        assembly_metadata={},
        llm_client=SimpleNamespace(complete=AsyncMock(return_value=findings_json)),
        tools={},
    )
    pattern = ResearchPattern(config={})
    pattern.context = ctx
    pattern.call_tool = run_tool
    result = await pattern.execute()
    assert isinstance(result, ResearchFindings)
    assert mcp_call_count["n"] == 1
    assert rest_call_count["n"] == 1
