from __future__ import annotations

import pytest

from openagent_agent_builder.builder import build_openagent_skill_output
from openagent_agent_builder.models import OpenAgentSkillInput


@pytest.mark.asyncio
async def test_build_openagent_skill_output_returns_spec_rationale_and_smoke_result():
    output = await build_openagent_skill_output(
        OpenAgentSkillInput(
            task_goal="Review a patch",
            agent_role="reviewer",
            agent_mode="team-role",
            workspace_root="C:/repo",
            handoff_expectation={"input": "patch", "output": "findings"},
        )
    )

    assert output.agent_spec["agent_key"] == "reviewer"
    assert output.smoke_result["status"] == "passed"
    assert "reviewer" in output.design_rationale.lower()
    assert output.handoff_contract["expected_output"] == "findings"
    assert output.integration_hints["agent_mode"] == "team-role"
