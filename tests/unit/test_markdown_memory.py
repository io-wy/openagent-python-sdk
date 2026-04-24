from __future__ import annotations

from types import SimpleNamespace

import pytest

from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory


def _ctx():
    return SimpleNamespace(
        state={},
        memory_view={},
        input_text="",
        tool_results=[],
    )


@pytest.mark.asyncio
async def test_inject_with_empty_dir(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    ctx = _ctx()
    await mem.inject(ctx)
    assert ctx.memory_view["user_goals"] == []
    assert ctx.memory_view["user_feedback"] == []


@pytest.mark.asyncio
async def test_capture_then_inject_roundtrip(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    mem.capture("user_feedback", "用 Arial 做英文正文", "用户 2026-04 明确要求")
    ctx = _ctx()
    await mem.inject(ctx)
    entries = ctx.memory_view["user_feedback"]
    assert len(entries) == 1
    assert "Arial" in entries[0]["rule"]
    assert (tmp_path / "MEMORY.md").exists()
    assert (tmp_path / "user_feedback.md").exists()


@pytest.mark.asyncio
async def test_writeback_drains_pending(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    ctx = _ctx()
    ctx.state["_pending_memory_writes"] = [
        {"category": "decisions", "rule": "palette=ocean", "reason": "user chose at stage 5"},
    ]
    await mem.writeback(ctx)
    assert ctx.state["_pending_memory_writes"] == []
    text = (tmp_path / "decisions.md").read_text(encoding="utf-8")
    assert "palette=ocean" in text


@pytest.mark.asyncio
async def test_unknown_category_falls_back_to_feedback(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    mem.capture("bogus_category", "rule X", "why")
    ctx = _ctx()
    await mem.inject(ctx)
    assert any("rule X" in e["rule"] for e in ctx.memory_view["user_feedback"])


@pytest.mark.asyncio
async def test_section_char_truncation(tmp_path):
    mem = MarkdownMemory(
        config={"memory_dir": str(tmp_path), "max_chars_per_section": 200},
    )
    for i in range(20):
        mem.capture("user_feedback", f"rule {i}" + "x" * 50, "why")
    ctx = _ctx()
    await mem.inject(ctx)
    entries = ctx.memory_view["user_feedback"]
    total = sum(len(e["rule"]) + len(e["reason"]) for e in entries)
    assert total <= 220  # small slack for format
    assert any("rule 19" in e["rule"] for e in entries)


@pytest.mark.asyncio
async def test_retrieve_keyword(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    mem.capture("user_goals", "make short pitch decks", "")
    mem.capture("user_goals", "prefer English title case", "")
    hits = await mem.retrieve("pitch", _ctx())
    assert len(hits) == 1
    assert "pitch" in hits[0]["rule"]


@pytest.mark.asyncio
async def test_forget(tmp_path):
    mem = MarkdownMemory(config={"memory_dir": str(tmp_path)})
    entry_id = mem.capture("user_feedback", "rule A", "why")
    assert mem.forget(entry_id) is True
    ctx = _ctx()
    await mem.inject(ctx)
    assert ctx.memory_view["user_feedback"] == []
