from __future__ import annotations

from openagent_agent_builder.archetypes import resolve_archetype
from openagent_agent_builder.models import OpenAgentSkillInput
from openagent_agent_builder.render import render_agent_spec


def test_render_agent_spec_outputs_single_agent_appconfig_bundle():
    spec = render_agent_spec(
        OpenAgentSkillInput(
            task_goal="Review a patch",
            agent_role="reviewer",
            agent_mode="team-role",
            workspace_root="C:/repo",
            available_tools=["read_file", "ripgrep"],
        ),
        archetype=resolve_archetype("reviewer"),
    )

    assert spec["sdk_config"]["version"] == "1.0"
    assert len(spec["sdk_config"]["agents"]) == 1
    assert spec["run_request_template"]["agent_id"] == spec["agent_key"]
    assert spec["run_request_template"]["context_hints"]["workspace_root"] == "C:/repo"
    assert [tool["id"] for tool in spec["sdk_config"]["agents"][0]["tools"]] == ["read_file", "ripgrep"]
