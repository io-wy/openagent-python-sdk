"""Tests for ``SubAgentTool`` preferring the handwritten local runner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from examples.corecoder_agent.app.tools.sub_agent import SubAgentTool

from ._helpers import make_ctx


class _FakeRunner:
    async def run(self, *, agent_id: str, session_id: str, input_text: str) -> str:
        return f"{agent_id}:{session_id}:{input_text}"


@pytest.mark.asyncio
async def test_sub_agent_uses_context_runner_without_runtime_config() -> None:
    tool = SubAgentTool(config={"sub_agent_id": "corecoder-subagent"})
    ctx = make_ctx(
        session_id="parent-session",
        deps=SimpleNamespace(corecoder_runner=_FakeRunner()),
    )

    result = await tool.invoke({"task": "inspect repo"}, ctx)

    assert result["session_id"].startswith("parent-session-sub-")
    assert result["result"].startswith("corecoder-subagent:")
    assert "inspect repo" in result["result"]
