from __future__ import annotations

from pathlib import Path


def test_openagent_agent_builder_skill_files_exist():
    root = Path("skills/openagent-agent-builder")

    assert (root / "SKILL.md").exists()
    assert (root / "agents" / "openai.yaml").exists()
    assert (root / "references" / "architecture.md").exists()
    assert (root / "references" / "examples.md").exists()
    assert (root / "src" / "openagent_agent_builder" / "__init__.py").exists()
    assert (root / "src" / "openagent_agent_builder" / "entrypoint.py").exists()


def test_openagent_agent_builder_docs_exist():
    assert Path("docs/openagent-agent-builder.md").exists()
