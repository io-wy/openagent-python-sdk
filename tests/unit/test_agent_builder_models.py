from __future__ import annotations

from openagent_agent_builder.models import OpenAgentSkillInput, OpenAgentSkillOutput


def test_openagent_skill_models_capture_v0_contract():
    payload = OpenAgentSkillInput(
        task_goal="Review a patch",
        agent_role="reviewer",
        agent_mode="team-role",
        workspace_root="C:/repo",
    )
    output = OpenAgentSkillOutput(
        agent_spec={"agent_key": "reviewer"},
        agent_prompt_summary="review code",
        design_rationale="use reviewer archetype",
        handoff_contract={"input": "diff", "output": "findings"},
        integration_hints={"mode": "team-role"},
        smoke_result={"status": "skipped"},
        next_actions=["wire into runner"],
    )

    assert payload.task_goal == "Review a patch"
    assert payload.agent_role == "reviewer"
    assert payload.agent_mode == "team-role"
    assert payload.smoke_run is True
    assert output.agent_spec["agent_key"] == "reviewer"
    assert output.next_actions == ["wire into runner"]
