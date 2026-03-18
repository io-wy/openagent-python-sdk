import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import CapabilityError, PluginLoadError
from openagents.plugins.loader import load_agent_plugins


def _base_payload() -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "demo-agent",
                "memory": {"type": "window_buffer"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock"},
                "tools": [{"id": "search", "type": "builtin_search"}],
            }
        ],
    }


def test_load_agent_plugins_builtin_types():
    config = load_config_dict(_base_payload())
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "WindowBufferMemory"
    assert type(plugins.pattern).__name__ == "ReActPattern"
    assert "search" in plugins.tools
    assert type(plugins.tools["search"]).__name__ == "BuiltinSearchTool"


def test_load_agent_plugins_impl_types():
    payload = _base_payload()
    payload["agents"][0]["memory"] = {"impl": "tests.fixtures.custom_plugins.CustomMemory"}
    payload["agents"][0]["pattern"] = {"impl": "tests.fixtures.custom_plugins.CustomPattern"}
    payload["agents"][0]["tools"] = [
        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
    ]
    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "CustomMemory"
    assert type(plugins.pattern).__name__ == "CustomPattern"
    assert type(plugins.tools["custom_tool"]).__name__ == "CustomTool"


def test_load_agent_plugins_rejects_pattern_without_react_capability():
    payload = _base_payload()
    payload["agents"][0]["pattern"] = {
        "impl": "tests.fixtures.custom_plugins.BadPatternNoCapability"
    }
    config = load_config_dict(payload)

    with pytest.raises(CapabilityError, match="missing required capabilities"):
        load_agent_plugins(config.agents[0])


def test_load_agent_plugins_rejects_unknown_builtin_type():
    payload = _base_payload()
    payload["agents"][0]["memory"] = {"type": "unknown_memory"}
    config = load_config_dict(payload)

    with pytest.raises(PluginLoadError, match="Unknown memory plugin type"):
        load_agent_plugins(config.agents[0])


def test_load_decorator_registered_plugins():
    """Test that plugins registered via decorators can be loaded."""
    # Import to trigger decorator registration
    from tests.fixtures import decorator_plugins  # noqa: F401

    payload = _base_payload()
    payload["agents"][0]["memory"] = {"type": "DecoratorMemory"}
    payload["agents"][0]["pattern"] = {"type": "DecoratorPattern"}
    payload["agents"][0]["tools"] = [{"id": "my_tool", "type": "decorated_tool"}]

    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "DecoratorMemory"
    assert type(plugins.pattern).__name__ == "DecoratorPattern"
    assert "my_tool" in plugins.tools
    assert type(plugins.tools["my_tool"]).__name__ == "DecoratorTool"


def test_type_and_impl_both_provided_uses_impl():
    """Test that when both type and impl are provided, impl takes priority."""
    payload = _base_payload()
    # Both type and impl - impl should win
    payload["agents"][0]["pattern"] = {"type": "react", "impl": "tests.fixtures.custom_plugins.CustomPattern"}

    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    # Should load CustomPattern, not ReActPattern
    assert type(plugins.pattern).__name__ == "CustomPattern"


def test_chain_memory_combines_multiple_memories():
    """Test that chain memory combines multiple memory plugins."""
    from openagents.plugins.loader import load_memory_plugin
    from openagents.config.schema import MemoryRef

    ref = MemoryRef(
        type="chain",
        config={
            "memories": [
                {"type": "buffer"},
                {"type": "window_buffer", "config": {"window_size": 5}},
            ]
        },
    )

    chain = load_memory_plugin(ref)
    assert type(chain).__name__ == "ChainMemory"
    assert len(chain._memories) == 2
    assert type(chain._memories[0]).__name__ == "BufferMemory"
    assert type(chain._memories[1]).__name__ == "WindowBufferMemory"

