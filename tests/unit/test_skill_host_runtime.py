from __future__ import annotations

from pathlib import Path

import pytest

from openagents.plugins.builtin.skills.local import LocalSkillsManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
OPENAGENT_SKILL_ROOT = SKILLS_ROOT / "openagent-agent-builder"


class _SessionStore:
    def __init__(self) -> None:
        self._states: dict[str, dict] = {}

    async def get_state(self, session_id: str) -> dict:
        return dict(self._states.get(session_id, {}))

    async def set_state(self, session_id: str, state: dict) -> None:
        self._states[session_id] = dict(state)


def test_load_skill_package_reads_skill_metadata_and_references():
    manager = LocalSkillsManager({"search_paths": [str(SKILLS_ROOT)], "enabled": ["openagent-agent-builder"]})
    package = manager._discover()["openagent-agent-builder"]

    assert package["name"] == "openagent-agent-builder"
    assert package["description"].startswith("Build one runnable OpenAgents single-agent spec")
    assert package["display_name"] == "OpenAgent Agent Builder"
    assert package["default_prompt"].startswith("Build one OpenAgents single-agent spec")
    assert package["entrypoint_file"] == OPENAGENT_SKILL_ROOT / "src" / "openagent_agent_builder" / "entrypoint.py"
    assert package["references"] == [
        OPENAGENT_SKILL_ROOT / "references" / "architecture.md",
        OPENAGENT_SKILL_ROOT / "references" / "examples.md",
    ]


def test_discover_skill_packages_finds_openagent_agent_builder():
    manager = LocalSkillsManager({"search_paths": [str(SKILLS_ROOT)]})
    names = sorted(manager._discover())

    assert names == ["openagent-agent-builder"]


def test_load_skill_package_rejects_missing_skill_md(tmp_path):
    broken = tmp_path / "broken-skill"
    broken.mkdir()

    assert LocalSkillsManager({"search_paths": [str(tmp_path)]})._discover() == {}


@pytest.mark.asyncio
async def test_run_skill_package_executes_entrypoint():
    manager = LocalSkillsManager({"search_paths": [str(SKILLS_ROOT)]})
    session = _SessionStore()

    prepared = await manager.prepare_session(session_id="s1", session_manager=session)
    references = await manager.load_references(
        session_id="s1",
        skill_name="openagent-agent-builder",
        session_manager=session,
    )
    result = await manager.run_skill(
        session_id="s1",
        skill_name="openagent-agent-builder",
        payload={
            "task_goal": "Review a patch",
            "agent_role": "reviewer",
            "agent_mode": "team-role",
            "workspace_root": "C:/repo",
        },
        session_manager=session,
    )

    assert "openagent-agent-builder" in prepared
    assert references[0]["path"].endswith("architecture.md")
    assert result["agent_spec"]["agent_key"] == "reviewer"
    assert result["smoke_result"]["status"] == "passed"
    assert result["integration_hints"]["agent_mode"] == "team-role"
