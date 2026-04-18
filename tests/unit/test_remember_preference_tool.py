from __future__ import annotations

from types import SimpleNamespace

import pytest

from openagents.plugins.builtin.tool.memory_tools import RememberPreferenceTool


def _ctx():
    return SimpleNamespace(state={})


@pytest.mark.asyncio
async def test_appends_to_pending():
    tool = RememberPreferenceTool(config={})
    ctx = _ctx()
    result = await tool.invoke(
        {"category": "user_feedback", "rule": "use Arial", "reason": "user said so"},
        ctx,
    )
    assert result["queued"] is True
    pending = ctx.state["_pending_memory_writes"]
    assert len(pending) == 1
    assert pending[0]["rule"] == "use Arial"


@pytest.mark.asyncio
async def test_multiple_calls_accumulate():
    tool = RememberPreferenceTool(config={})
    ctx = _ctx()
    await tool.invoke({"category": "user_goals", "rule": "R1", "reason": ""}, ctx)
    await tool.invoke({"category": "decisions", "rule": "R2", "reason": "x"}, ctx)
    assert len(ctx.state["_pending_memory_writes"]) == 2
