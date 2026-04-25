"""Tests for wiki agent plugins (pattern, memory, context assembler, fetch tool)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from examples.llm_wiki_agent.app.plugins import (
    DeepReadUrlTool,
    WikiContextAssembler,
    WikiMemory,
    WikiPattern,
)
from examples.llm_wiki_agent.app.protocols import WikiSource
from examples.llm_wiki_agent.app.store import WikiKnowledgeStore


class TestWikiPattern:
    def test_llm_system_prompt(self) -> None:
        pattern = WikiPattern()
        prompt = pattern._llm_system_prompt()
        assert "Wiki Agent" in prompt
        assert "deep_read_url" in prompt or "tool_call" in prompt

    def test_parse_llm_action_json(self) -> None:
        pattern = WikiPattern()
        result = pattern._parse_llm_action('{"type": "tool_call", "tool": "search_kb", "params": {"query": "AI"}}')
        assert result["type"] == "tool_call"
        assert result["tool"] == "search_kb"

    def test_parse_llm_action_final(self) -> None:
        pattern = WikiPattern()
        result = pattern._parse_llm_action('{"type": "final", "content": "hello"}')
        assert result["type"] == "final"
        assert result["content"] == "hello"

    def test_parse_llm_action_plain_text(self) -> None:
        pattern = WikiPattern()
        result = pattern._parse_llm_action("Just a plain answer.")
        assert result["type"] == "final"
        assert result["content"] == "Just a plain answer."

    @pytest.mark.asyncio
    async def test_resolve_followup_list_sources(self) -> None:
        pattern = WikiPattern()
        ctx = MagicMock()
        ctx.input_text = "What sources do you have?"
        ctx.memory_view = {"wiki_kb_stats": {"source_count": 3, "topics": ["AI", "ML"]}}
        res = await pattern.resolve_followup(context=ctx)
        assert res is not None
        assert res.status == "resolved"
        assert "3 source" in res.output

    @pytest.mark.asyncio
    async def test_resolve_followup_no_match(self) -> None:
        pattern = WikiPattern()
        ctx = MagicMock()
        ctx.input_text = "What is the transformer architecture?"
        ctx.memory_view = {}
        res = await pattern.resolve_followup(context=ctx)
        assert res is None


class TestWikiMemory:
    @pytest.mark.asyncio
    async def test_inject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WikiKnowledgeStore(tmp)
            store.add_source(
                WikiSource(url="https://a.com", title="A", fetched_at="", content_hash="", metadata={}),
                [],
            )
            mem = WikiMemory({"storage_dir": tmp, "session_dir": tmp})
            ctx = MagicMock()
            ctx.memory_view = {}
            await mem.inject(ctx)
            assert ctx.memory_view["wiki_kb_stats"]["source_count"] == 1

    @pytest.mark.asyncio
    async def test_writeback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = WikiMemory({"storage_dir": tmp, "session_dir": tmp})
            ctx = MagicMock()
            ctx.session_id = "s1"
            ctx.run_id = "r1"
            ctx.input_text = "hello"
            ctx.tool_results = []
            await mem.writeback(ctx)
            session_file = Path(tmp) / "s1.jsonl"
            assert session_file.exists()
            content = session_file.read_text(encoding="utf-8")
            assert "s1" in content
            assert "hello" in content


class TestWikiContextAssembler:
    @pytest.mark.asyncio
    async def test_assemble_empty_kb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asm = WikiContextAssembler({"knowledge_dir": tmp})
            result = await asm.assemble(
                request=MagicMock(),
                session_state={},
                session_manager=MagicMock(),
            )
            assert result.transcript[0]["role"] == "system"
            assert "empty" in result.transcript[0]["content"].lower()
            assert result.metadata["wiki_kb_ready"] is False

    @pytest.mark.asyncio
    async def test_assemble_with_kb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WikiKnowledgeStore(tmp)
            store.add_source(
                WikiSource(url="https://a.com", title="A", fetched_at="", content_hash="", metadata={}),
                [],
            )
            asm = WikiContextAssembler({"knowledge_dir": tmp})
            result = await asm.assemble(
                request=MagicMock(),
                session_state={},
                session_manager=MagicMock(),
            )
            assert "1 source" in result.transcript[0]["content"]
            assert result.metadata["wiki_kb_ready"] is True


class TestDeepReadUrlTool:
    @pytest.mark.asyncio
    async def test_invoke_missing_url(self) -> None:
        tool = DeepReadUrlTool()
        result = await tool.invoke({}, None)
        assert result["success"] is False
        assert "url is required" in result["error"]

    def test_schema(self) -> None:
        tool = DeepReadUrlTool()
        schema = tool.schema()
        assert "url" in schema["properties"]
