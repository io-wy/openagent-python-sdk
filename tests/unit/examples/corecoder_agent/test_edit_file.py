"""Tests for ``EditFileTool`` — the strict-uniqueness search-and-replace tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from openagents.errors.exceptions import ModelRetryError, ToolError

from examples.corecoder_agent.app.tools.edit_file import EditFileTool

from ._helpers import make_ctx


@pytest.mark.asyncio
async def test_edit_file_replaces_unique_substring(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    tool = EditFileTool()
    ctx = make_ctx()
    result = await tool.invoke(
        {
            "file_path": str(target),
            "old_string": "return a + b",
            "new_string": "return a + b + 1",
        },
        ctx,
    )

    assert result["occurrences_replaced"] == 1
    assert "return a + b + 1" in target.read_text(encoding="utf-8")
    assert "diff" in result and "+1" in result["diff"]
    # Dirty-file tracking
    dirty = ctx.scratch.get("dirty_files")
    assert isinstance(dirty, set)
    assert any(p.endswith("demo.py") for p in dirty)


@pytest.mark.asyncio
async def test_edit_file_zero_matches_raises_model_retry(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("hello world\n", encoding="utf-8")

    tool = EditFileTool()
    with pytest.raises(ModelRetryError, match="not found"):
        await tool.invoke(
            {
                "file_path": str(target),
                "old_string": "DOES NOT APPEAR",
                "new_string": "x",
            },
            make_ctx(),
        )


@pytest.mark.asyncio
async def test_edit_file_multiple_matches_raises_model_retry(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("a\na\na\n", encoding="utf-8")

    tool = EditFileTool()
    with pytest.raises(ModelRetryError, match=r"appears 3 times"):
        await tool.invoke(
            {
                "file_path": str(target),
                "old_string": "a",
                "new_string": "b",
            },
            make_ctx(),
        )


@pytest.mark.asyncio
async def test_edit_file_missing_file_raises_tool_error(tmp_path: Path) -> None:
    tool = EditFileTool()
    with pytest.raises(ToolError, match="File not found"):
        await tool.invoke(
            {
                "file_path": str(tmp_path / "ghost.py"),
                "old_string": "x",
                "new_string": "y",
            },
            make_ctx(),
        )


@pytest.mark.asyncio
async def test_edit_file_validates_required_args() -> None:
    tool = EditFileTool()
    with pytest.raises(ToolError, match="file_path is required"):
        await tool.invoke({"old_string": "a", "new_string": "b"}, make_ctx())
    with pytest.raises(ToolError, match="old_string"):
        await tool.invoke(
            {"file_path": "x.py", "old_string": "", "new_string": "b"}, make_ctx()
        )
