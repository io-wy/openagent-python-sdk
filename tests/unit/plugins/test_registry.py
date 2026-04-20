"""Smoke tests: new builtins are registered and loadable via the plugin loader."""

from __future__ import annotations

import pytest

from openagents.config.schema import MemoryRef, ToolRef
from openagents.plugins.loader import load_memory_plugin, load_tool_plugin


@pytest.mark.parametrize(
    "type_name,config_overrides",
    [
        ("shell_exec", {}),
        ("tavily_search", {}),
        ("remember_preference", {}),
    ],
)
def test_tool_registered(type_name: str, config_overrides: dict) -> None:
    ref = ToolRef(id=type_name, type=type_name, config=config_overrides)
    plugin = load_tool_plugin(ref)
    assert plugin is not None
    assert hasattr(plugin, "capabilities")


def test_markdown_memory_registered(tmp_path) -> None:
    ref = MemoryRef(type="markdown_memory", config={"memory_dir": str(tmp_path)})
    plugin = load_memory_plugin(ref)
    assert plugin is not None
    assert hasattr(plugin, "capabilities")
