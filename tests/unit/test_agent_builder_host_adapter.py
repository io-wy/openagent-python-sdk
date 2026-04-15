from __future__ import annotations

import pytest

from openagent_agent_builder.entrypoint import run_openagent_skill


@pytest.mark.asyncio
async def test_run_openagent_skill_wraps_shared_builder():
    result = await run_openagent_skill(
        {
            "task_goal": "Review a patch",
            "agent_role": "reviewer",
            "agent_mode": "team-role",
            "workspace_root": "C:/repo",
        }
    )

    assert result["agent_spec"]["agent_key"] == "reviewer"
    assert result["smoke_result"]["status"] == "passed"
