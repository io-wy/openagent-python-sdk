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


def test_render_agent_spec_emits_filesystem_aware_executor_when_workspace_root_set():
    """A workspace_root should win over the archetype's plain 'safe' executor
    and emit a filesystem_aware tool_executor with read/write sandbox roots."""
    spec = render_agent_spec(
        OpenAgentSkillInput(
            task_goal="Edit files",
            agent_role="coder",
            agent_mode="team-role",
            workspace_root="C:/repo",
            available_tools=["read_file", "write_file"],
        ),
        archetype=resolve_archetype("coder"),
    )

    agent = spec["sdk_config"]["agents"][0]
    assert agent["tool_executor"]["type"] == "filesystem_aware"
    fs_config = agent["tool_executor"]["config"]
    assert fs_config["read_roots"] == ["C:/repo"]
    assert fs_config["write_roots"] == ["C:/repo"]
    assert fs_config["allow_tools"] == ["read_file", "write_file"]


def test_render_agent_spec_omits_removed_seams():
    """The execution_policy, followup_resolver, and response_repair_policy
    keys were consolidated away and must not appear in generated specs."""
    spec = render_agent_spec(
        OpenAgentSkillInput(
            task_goal="Review a patch",
            agent_role="reviewer",
            agent_mode="team-role",
            workspace_root="C:/repo",
        ),
        archetype=resolve_archetype("reviewer"),
    )

    agent = spec["sdk_config"]["agents"][0]
    assert "execution_policy" not in agent
    assert "followup_resolver" not in agent
    assert "response_repair_policy" not in agent


def test_render_agent_spec_falls_back_to_archetype_executor_without_workspace_root():
    """Without a workspace_root or tool_ids, we keep the archetype's safe executor."""
    spec = render_agent_spec(
        OpenAgentSkillInput(
            task_goal="Review a patch",
            agent_role="reviewer",
            agent_mode="subagent",
            available_tools=[],  # clears filter, but coder keeps its archetype tools
        ),
        archetype=resolve_archetype("reviewer"),
    )

    agent = spec["sdk_config"]["agents"][0]
    # reviewer archetype ships a plain `safe` executor; without workspace_root
    # we should preserve it rather than overriding with filesystem_aware.
    assert agent["tool_executor"]["type"] == "safe"
