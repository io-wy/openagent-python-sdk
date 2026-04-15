from __future__ import annotations

import pytest

from openagent_agent_builder.archetypes import resolve_archetype
from openagent_agent_builder.models import OpenAgentSkillInput
from openagent_agent_builder.render import render_agent_spec
from openagent_agent_builder.smoke import smoke_run_agent_spec


@pytest.mark.asyncio
async def test_smoke_run_agent_spec_returns_passed_result_for_valid_spec():
    spec = render_agent_spec(
        OpenAgentSkillInput(
            task_goal="Say hello",
            agent_role="reviewer",
            agent_mode="subagent",
        ),
        archetype=resolve_archetype("reviewer"),
    )

    result = await smoke_run_agent_spec(spec_bundle=spec, smoke_input="hello")

    assert result["status"] == "passed"
    assert result["agent_id"] == spec["agent_key"]
    assert "result" in result
