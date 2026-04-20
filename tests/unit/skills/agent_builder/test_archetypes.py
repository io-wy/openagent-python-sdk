from __future__ import annotations

import pytest
from openagent_agent_builder.archetypes import list_archetypes, resolve_archetype


def test_resolve_archetype_returns_reviewer_defaults():
    archetype = resolve_archetype("reviewer")

    assert archetype["agent_name"] == "Patch Reviewer"
    assert archetype["pattern"]["type"] == "react"
    assert "read_file" in [tool["id"] for tool in archetype["tools"]]


def test_list_archetypes_contains_v0_roles():
    assert list_archetypes() == ["coder", "planner", "researcher", "reviewer"]


def test_resolve_archetype_rejects_unknown_role():
    with pytest.raises(ValueError, match="Unknown archetype"):
        resolve_archetype("unknown")
