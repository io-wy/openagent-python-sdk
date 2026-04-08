import json

import pytest

from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError


def _base_config() -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "demo-agent",
                "memory": {"type": "window_buffer", "on_error": "continue"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock"},
                "tools": [
                    {"id": "search", "type": "builtin_search", "enabled": True},
                    {
                        "id": "weather",
                        "impl": "my_plugins.tools.weather.WeatherTool",
                        "enabled": True,
                    },
                ],
                "runtime": {
                    "max_steps": 16,
                    "step_timeout_ms": 30000,
                    "session_queue_size": 1000,
                    "event_queue_size": 2000,
                },
            }
        ],
    }


def _write(tmp_path, payload: dict) -> str:
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_load_config_valid_config(tmp_path):
    config_path = _write(tmp_path, _base_config())
    config = load_config(config_path)

    assert config.version == "1.0"
    assert len(config.agents) == 1
    assert config.agents[0].memory.type == "window_buffer"
    assert config.agents[0].pattern.type == "react"


def test_load_config_accepts_skill_config(tmp_path):
    payload = _base_config()
    payload["agents"][0]["skill"] = {
        "impl": "tests.fixtures.custom_plugins.CustomSkill",
        "config": {"focus": "training"},
    }
    config_path = _write(tmp_path, payload)

    config = load_config(config_path)
    assert config.agents[0].skill is not None
    assert config.agents[0].skill.impl == "tests.fixtures.custom_plugins.CustomSkill"


def test_load_config_accepts_agent_level_runtime_seams(tmp_path):
    payload = _base_config()
    payload["agents"][0]["tool_executor"] = {
        "impl": "tests.fixtures.custom_plugins.CustomToolExecutor",
        "config": {"name": "agent-level"},
    }
    payload["agents"][0]["execution_policy"] = {
        "impl": "tests.fixtures.custom_plugins.CustomExecutionPolicy",
    }
    payload["agents"][0]["context_assembler"] = {
        "impl": "tests.fixtures.custom_plugins.CustomContextAssembler",
    }
    config_path = _write(tmp_path, payload)

    config = load_config(config_path)
    assert config.agents[0].tool_executor is not None
    assert config.agents[0].execution_policy is not None
    assert config.agents[0].context_assembler is not None


def test_load_config_accepts_type_and_impl(tmp_path):
    """Test that both type and impl can be provided together (impl takes priority)."""
    payload = _base_config()
    payload["agents"][0]["memory"] = {
        "type": "window_buffer",
        "impl": "my_plugins.memory.FileMemory",
    }
    config_path = _write(tmp_path, payload)

    config = load_config(config_path)
    assert config.agents[0].memory.type == "window_buffer"
    assert config.agents[0].memory.impl == "my_plugins.memory.FileMemory"


def test_load_config_rejects_missing_type_and_impl(tmp_path):
    payload = _base_config()
    payload["agents"][0]["pattern"] = {"config": {}}
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="at least one of 'type' or 'impl'"):
        load_config(config_path)


def test_load_config_rejects_skill_missing_type_and_impl(tmp_path):
    payload = _base_config()
    payload["agents"][0]["skill"] = {"config": {"focus": "training"}}
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="agents\\['assistant'\\]\\.skill"):
        load_config(config_path)


def test_load_config_rejects_context_assembler_missing_type_and_impl(tmp_path):
    payload = _base_config()
    payload["agents"][0]["context_assembler"] = {"config": {"marker": "x"}}
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="agents\\['assistant'\\]\\.context_assembler"):
        load_config(config_path)


def test_load_config_rejects_duplicate_tool_ids(tmp_path):
    payload = _base_config()
    payload["agents"][0]["tools"] = [
        {"id": "search", "type": "builtin_search"},
        {"id": "search", "impl": "my_plugins.tools.weather.WeatherTool"},
    ]
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="Duplicate tool id"):
        load_config(config_path)


def test_load_config_accepts_mock_llm(tmp_path):
    payload = _base_config()
    payload["agents"][0]["llm"] = {
        "provider": "mock",
        "model": "mock-react-v1",
        "temperature": 0,
    }
    config_path = _write(tmp_path, payload)
    config = load_config(config_path)
    assert config.agents[0].llm is not None
    assert config.agents[0].llm.provider == "mock"


def test_load_config_accepts_anthropic_llm(tmp_path):
    payload = _base_config()
    payload["agents"][0]["llm"] = {
        "provider": "anthropic",
        "model": "claude-3-haiku-20240307",
        "api_key_env": "ANTHROPIC_API_KEY",
    }
    config_path = _write(tmp_path, payload)

    config = load_config(config_path)
    assert config.agents[0].llm is not None
    assert config.agents[0].llm.provider == "anthropic"


def test_load_config_accepts_missing_llm(tmp_path):
    payload = _base_config()
    payload["agents"][0].pop("llm")
    config_path = _write(tmp_path, payload)

    config = load_config(config_path)
    assert config.agents[0].llm is None


def test_load_config_rejects_unknown_llm_provider(tmp_path):
    payload = _base_config()
    payload["agents"][0]["llm"] = {"provider": "unknown_provider"}
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="llm"):
        load_config(config_path)


def test_load_config_rejects_openai_compatible_without_api_base(tmp_path):
    payload = _base_config()
    payload["agents"][0]["llm"] = {
        "provider": "openai_compatible",
        "model": "gpt-4o-mini",
    }
    config_path = _write(tmp_path, payload)

    with pytest.raises(ConfigError, match="api_base"):
        load_config(config_path)
