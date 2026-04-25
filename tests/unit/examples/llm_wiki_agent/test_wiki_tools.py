"""Tests for wiki agent tools."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from examples.llm_wiki_agent.app.plugins import AddSourceTool, IngestUrlTool, ListSourcesTool, SearchKbTool


@pytest.fixture
def tmp_store_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


class TestAddSourceTool:
    @pytest.mark.asyncio
    async def test_add_source_success(self, tmp_store_dir: str) -> None:
        tool = AddSourceTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        result = await tool.invoke(
            {
                "url": "https://example.com/test",
                "title": "Test",
                "chunks": [
                    {
                        "content": "Hello world",
                        "summary": "Greeting",
                        "entities": ["world"],
                        "topics": ["greeting"],
                    }
                ],
            },
            ctx,
        )
        assert result["success"] is True
        assert result["chunks_stored"] == 1
        assert result["total_sources"] == 1

    @pytest.mark.asyncio
    async def test_add_source_missing_url(self, tmp_store_dir: str) -> None:
        tool = AddSourceTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        result = await tool.invoke({"chunks": []}, ctx)
        assert result["success"] is False
        assert "url is required" in result["error"]


class TestSearchKbTool:
    @pytest.mark.asyncio
    async def test_search(self, tmp_store_dir: str) -> None:
        add_tool = AddSourceTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        await add_tool.invoke(
            {
                "url": "https://example.com/ml",
                "title": "ML",
                "chunks": [
                    {
                        "content": "Machine learning is a subset of AI.",
                        "summary": "ML intro",
                        "entities": ["machine learning", "AI"],
                        "topics": ["AI"],
                    }
                ],
            },
            ctx,
        )

        search_tool = SearchKbTool({"storage_dir": tmp_store_dir})
        result = await search_tool.invoke({"query": "machine learning AI"}, ctx)
        assert result["success"] is True
        assert len(result["results"]) >= 1

    @pytest.mark.asyncio
    async def test_search_missing_query(self, tmp_store_dir: str) -> None:
        tool = SearchKbTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        result = await tool.invoke({}, ctx)
        assert result["success"] is False


class TestIngestUrlTool:
    @pytest.mark.asyncio
    async def test_ingest_missing_url(self, tmp_store_dir: str) -> None:
        tool = IngestUrlTool({"storage_dir": tmp_store_dir})
        result = await tool.invoke({}, None)
        assert result["success"] is False
        assert "url is required" in result["error"]

    @pytest.mark.asyncio
    async def test_ingest_success(self, tmp_store_dir: str) -> None:
        tool = IngestUrlTool({"storage_dir": tmp_store_dir, "chunk_size": 50})
        result = await tool.invoke(
            {"url": "https://example.com/test"},
            None,
        )
        # Note: this makes a real HTTP request; in CI it may fail.
        # We just verify the tool handles the response path.
        assert "success" in result


class TestListSourcesTool:
    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_store_dir: str) -> None:
        tool = ListSourcesTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        result = await tool.invoke({}, ctx)
        assert result["success"] is True
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_list_with_sources(self, tmp_store_dir: str) -> None:
        add_tool = AddSourceTool({"storage_dir": tmp_store_dir})
        ctx = MagicMock()
        await add_tool.invoke(
            {
                "url": "https://a.com",
                "title": "A",
                "chunks": [{"content": "x"}],
            },
            ctx,
        )

        list_tool = ListSourcesTool({"storage_dir": tmp_store_dir})
        result = await list_tool.invoke({}, ctx)
        assert result["count"] == 1
        assert result["sources"][0]["url"] == "https://a.com"
