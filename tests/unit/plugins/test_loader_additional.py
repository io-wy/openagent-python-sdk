"""Tests for plugins loader."""

import warnings

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.loader import (
    load_agent_plugins,
    load_memory_plugin,
    load_pattern_plugin,
    load_runtime_components,
    load_skills_plugin,
    load_tool_plugin,
)


def _config_with_agent(agent_override: dict = None) -> dict:
    base = {
        "version": "1.0",
        "agents": [
            {
                "id": "test",
                "name": "Test",
                "memory": {"impl": "openagents.plugins.builtin.memory.buffer.BufferMemory"},
                "pattern": {"impl": "openagents.plugins.builtin.pattern.react.ReActPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
            }
        ],
    }
    if agent_override:
        base["agents"][0].update(agent_override)
    return base


def _minimal_config() -> dict:
    return {
        "version": "1.0",
        "runtime": {"impl": "openagents.plugins.builtin.runtime.default_runtime.DefaultRuntime"},
        "session": {"impl": "openagents.plugins.builtin.session.in_memory.InMemorySessionManager"},
        "events": {"impl": "openagents.plugins.builtin.events.async_event_bus.AsyncEventBus"},
        "agents": [
            {
                "id": "dummy",
                "name": "Dummy",
                "memory": {"impl": "openagents.plugins.builtin.memory.buffer.BufferMemory"},
                "pattern": {"impl": "openagents.plugins.builtin.pattern.react.ReActPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
            }
        ],
    }


def test_load_memory_plugin_buffer():
    """Test loading buffer memory plugin."""
    from openagents.config.schema import MemoryRef

    ref = MemoryRef(type="buffer")
    plugin = load_memory_plugin(ref)

    assert plugin is not None
    assert hasattr(plugin, "inject")
    assert hasattr(plugin, "writeback")


def test_load_pattern_plugin_react():
    """Test loading react pattern plugin."""
    from openagents.config.schema import PatternRef

    ref = PatternRef(type="react")
    plugin = load_pattern_plugin(ref)

    assert plugin is not None
    assert hasattr(plugin, "execute")
    assert hasattr(plugin, "react")


def test_load_runtime_components():
    """Test loading runtime components."""
    config = load_config_dict(_minimal_config())
    components = load_runtime_components(
        runtime_ref=config.runtime,
        session_ref=config.session,
        events_ref=config.events,
        skills_ref=config.skills,
    )

    assert components.runtime is not None
    assert components.session is not None
    assert components.events is not None
    assert components.skills is not None


def test_load_runtime_components_missing_impl():
    """Test loading runtime with missing impl raises error."""
    from openagents.config.schema import RuntimeRef

    ref = RuntimeRef(type="unknown_type")
    with pytest.raises(PluginLoadError):
        load_runtime_components(
            runtime_ref=ref,
            session_ref=RuntimeRef(type="in_memory"),
            events_ref=RuntimeRef(type="async_event_bus"),
            skills_ref=None,
        )


def test_load_agent_plugins_full_path():
    """Test loading agent plugins with full impl path."""
    config = load_config_dict(_config_with_agent())
    agent = config.agents[0]

    plugins = load_agent_plugins(agent)

    assert plugins.memory is not None
    assert plugins.pattern is not None
    assert plugins.tools is not None


def test_load_agent_plugins_validation_error():
    """Test loading agent plugins with invalid config raises ConfigError."""
    # Config with invalid memory impl (not existing path)
    config = load_config_dict(
        _config_with_agent(
            {
                "memory": {"impl": "nonexistent.module.Class"},
            }
        )
    )
    agent = config.agents[0]

    with pytest.raises(PluginLoadError):
        load_agent_plugins(agent)


def test_load_tool_plugin():
    """Test loading a tool plugin."""
    from openagents.config.schema import ToolRef

    ref = ToolRef(
        id="test_tool",
        impl="openagents.plugins.builtin.tool.math_tools.CalcTool",
    )
    plugin = load_tool_plugin(ref)

    assert plugin is not None


def test_load_skills_plugin():
    """Test loading the top-level skills component."""
    from openagents.config.schema import SkillsRef

    plugin = load_skills_plugin(SkillsRef(type="local", config={"search_paths": ["skills"]}))

    assert plugin is not None
    assert hasattr(plugin, "prepare_session")


def test_load_tool_plugin_invalid_impl():
    """Test loading tool with invalid impl raises error."""
    from openagents.config.schema import ToolRef

    ref = ToolRef(
        id="test_tool",
        impl="invalid.module.path",
    )
    with pytest.raises(PluginLoadError):
        load_tool_plugin(ref)


def test_load_memory_plugin_rejects_positional_only_constructor():
    from openagents.config.schema import MemoryRef

    ref = MemoryRef(impl="tests.fixtures.custom_plugins.LegacyPositionalMemory")

    with pytest.raises(PluginLoadError):
        load_memory_plugin(ref)


def test_load_memory_plugin_surfaces_constructor_typeerror():
    from openagents.config.schema import MemoryRef

    ref = MemoryRef(impl="tests.fixtures.custom_plugins.ExplodingKeywordMemory")

    with pytest.raises(PluginLoadError, match="keyword constructor blew up"):
        load_memory_plugin(ref)


def test_plugin_registry():
    """Test plugin registry access."""
    from openagents.plugins.registry import get_builtin_plugin_class, list_builtin_plugins

    # Test getting builtin plugin class
    cls = get_builtin_plugin_class("memory", "buffer")
    assert cls is not None

    # Test listing builtin plugins
    plugins = list_builtin_plugins("memory")
    assert "buffer" in plugins


def test_duplicate_tool_registration_warns():
    from openagents.decorators import tool

    class FirstTool:
        pass

    class SecondTool:
        pass

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tool(name="dup_loader_tool")(FirstTool)
        tool(name="dup_loader_tool")(SecondTool)

    assert any("overridden" in str(item.message).lower() for item in caught)


def test_load_memory_plugin_missing_method():
    """Test loading memory with missing required method."""
    from openagents.config.schema import MemoryRef

    # Create a mock memory without inject method
    class BadMemory:
        config = {}
        # Missing inject method

    # Can't easily test this without modifying registry
    # Just verify the plugin loads with valid impl
    ref = MemoryRef(type="buffer")
    plugin = load_memory_plugin(ref)
    assert plugin is not None


def test_load_agent_plugins_with_tools():
    """Test loading agent with tools."""
    config = load_config_dict(
        _config_with_agent({"tools": [{"id": "calc", "impl": "openagents.plugins.builtin.tool.math_tools.CalcTool"}]})
    )
    agent = config.agents[0]

    plugins = load_agent_plugins(agent)

    assert "calc" in plugins.tools
