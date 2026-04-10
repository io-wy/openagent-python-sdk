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
                "skill": {"impl": "tests.fixtures.custom_plugins.CustomSkill"},
                "tools": [{"id": "search", "type": "builtin_search"}],
            }
        ],
    }


def test_load_agent_plugins_builtin_types():
    payload = _base_payload()
    payload["agents"][0]["tool_executor"] = {"type": "safe"}
    payload["agents"][0]["execution_policy"] = {"type": "filesystem"}
    payload["agents"][0]["context_assembler"] = {"type": "summarizing"}
    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "WindowBufferMemory"
    assert type(plugins.pattern).__name__ == "ReActPattern"
    assert type(plugins.skill).__name__ == "CustomSkill"
    assert type(plugins.tool_executor).__name__ == "SafeToolExecutor"
    assert type(plugins.execution_policy).__name__ == "FilesystemExecutionPolicy"
    assert type(plugins.context_assembler).__name__ == "SummarizingContextAssembler"
    assert "search" in plugins.tools
    assert "skill_calc" in plugins.tools
    assert type(plugins.tools["search"]).__name__ == "BuiltinSearchTool"


def test_load_agent_plugins_impl_types():
    payload = _base_payload()
    payload["agents"][0]["memory"] = {"impl": "tests.fixtures.custom_plugins.CustomMemory"}
    payload["agents"][0]["pattern"] = {"impl": "tests.fixtures.custom_plugins.CustomPattern"}
    payload["agents"][0]["skill"] = {"impl": "tests.fixtures.custom_plugins.CustomSkill"}
    payload["agents"][0]["tool_executor"] = {"impl": "tests.fixtures.custom_plugins.CustomToolExecutor"}
    payload["agents"][0]["execution_policy"] = {"impl": "tests.fixtures.custom_plugins.CustomExecutionPolicy"}
    payload["agents"][0]["context_assembler"] = {"impl": "tests.fixtures.custom_plugins.CustomContextAssembler"}
    payload["agents"][0]["followup_resolver"] = {"impl": "tests.fixtures.custom_plugins.CustomFollowupResolver"}
    payload["agents"][0]["response_repair_policy"] = {
        "impl": "tests.fixtures.custom_plugins.CustomResponseRepairPolicy"
    }
    payload["agents"][0]["tools"] = [
        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
    ]
    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "CustomMemory"
    assert type(plugins.pattern).__name__ == "CustomPattern"
    assert type(plugins.skill).__name__ == "CustomSkill"
    assert type(plugins.tool_executor).__name__ == "CustomToolExecutor"
    assert type(plugins.execution_policy).__name__ == "CustomExecutionPolicy"
    assert type(plugins.context_assembler).__name__ == "CustomContextAssembler"
    assert type(plugins.followup_resolver).__name__ == "CustomFollowupResolver"
    assert type(plugins.response_repair_policy).__name__ == "CustomResponseRepairPolicy"
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
    payload["agents"][0]["skill"] = {"type": "decorated_skill"}
    payload["agents"][0]["tool_executor"] = {"type": "decorated_tool_executor"}
    payload["agents"][0]["execution_policy"] = {"type": "decorated_execution_policy"}
    payload["agents"][0]["context_assembler"] = {"type": "decorated_context_assembler"}
    payload["agents"][0]["followup_resolver"] = {"type": "decorated_followup_resolver"}
    payload["agents"][0]["response_repair_policy"] = {"type": "decorated_response_repair_policy"}
    payload["agents"][0]["tools"] = [{"id": "my_tool", "type": "decorated_tool"}]

    config = load_config_dict(payload)
    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.memory).__name__ == "DecoratorMemory"
    assert type(plugins.pattern).__name__ == "DecoratorPattern"
    assert type(plugins.skill).__name__ == "DecoratorSkill"
    assert type(plugins.tool_executor).__name__ == "DecoratorToolExecutor"
    assert type(plugins.execution_policy).__name__ == "DecoratorExecutionPolicy"
    assert type(plugins.context_assembler).__name__ == "DecoratorContextAssembler"
    assert type(plugins.followup_resolver).__name__ == "DecoratorFollowupResolver"
    assert type(plugins.response_repair_policy).__name__ == "DecoratorResponseRepairPolicy"
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


def test_load_agent_plugins_explicit_tools_override_skill_tools():
    payload = _base_payload()
    payload["agents"][0]["tools"] = [
        {"id": "search", "impl": "tests.fixtures.custom_plugins.CustomTool"},
    ]
    config = load_config_dict(payload)

    plugins = load_agent_plugins(config.agents[0])

    assert type(plugins.tools["search"]).__name__ == "CustomTool"
    assert "skill_calc" in plugins.tools


def test_load_agent_plugins_rejects_skill_without_capability():
    payload = _base_payload()
    payload["agents"][0]["skill"] = {
        "impl": "tests.fixtures.custom_plugins.BadSkillNoCapability"
    }
    config = load_config_dict(payload)

    with pytest.raises(CapabilityError, match="skill plugin"):
        load_agent_plugins(config.agents[0])


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
