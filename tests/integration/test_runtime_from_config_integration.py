from __future__ import annotations

import json
from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_runtime_from_quickstart_config_file(monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = Runtime.from_config("examples/quickstart/agent.json")

    first = await runtime.run(
        agent_id="assistant",
        session_id="quickstart",
        input_text="hello integration",
    )
    second = await runtime.run(
        agent_id="assistant",
        session_id="quickstart",
        input_text="/tool search runtime",
    )

    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first.startswith("Echo:")
    assert second.startswith("Tool[search] =>")

    state = await runtime.session_manager.get_state("quickstart")
    assert len(state.get("memory_buffer", [])) == 2


@pytest.mark.asyncio
async def test_runtime_from_custom_plugin_config_file(tmp_path):
    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "custom-agent",
                "name": "custom-agent",
                "memory": {"impl": "tests.fixtures.custom_plugins.CustomMemory"},
                "pattern": {"impl": "tests.fixtures.custom_plugins.CustomPattern"},
                "llm": {"provider": "mock"},
                "tools": [{"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}],
                "runtime": {"max_steps": 8, "step_timeout_ms": 1000},
            }
        ],
    }
    config_path = Path(tmp_path) / "agent.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    runtime = Runtime.from_config(config_path)
    result = await runtime.run(
        agent_id="custom-agent",
        session_id="custom-s1",
        input_text="hello",
    )

    assert result == "ok"
