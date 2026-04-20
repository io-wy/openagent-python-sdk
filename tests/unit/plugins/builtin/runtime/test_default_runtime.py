from __future__ import annotations

from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from openagents.config.loader import load_config, load_config_dict
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_quickstart_builtin_echo_flow(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://stub.invalid")
    monkeypatch.setenv("LLM_API_KEY", "stub-key")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    config = load_config(Path("examples/quickstart/agent.json"))
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = Runtime(config)

    result = await runtime.run(agent_id="assistant", session_id="demo", input_text="hello")

    assert isinstance(result, str)
    assert result.startswith("Echo: hello")
    assert "history=0" in result
    assert any(evt.name == "llm.called" for evt in runtime.event_bus.history)

    state = await runtime.session_manager.get_state("demo")
    assert isinstance(state.get("memory_buffer"), list)
    assert len(state["memory_buffer"]) == 1
    assert state["memory_buffer"][0]["input"] == "hello"


@pytest.mark.asyncio
async def test_builtin_react_tool_call_flow(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "http://stub.invalid")
    monkeypatch.setenv("LLM_API_KEY", "stub-key")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    config = load_config(Path("examples/quickstart/agent.json"))
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = Runtime(config)

    result = await runtime.run(
        agent_id="assistant",
        session_id="tool-sess",
        input_text="/tool search memory injection",
    )

    assert isinstance(result, str)
    assert result.startswith("Tool[search] =>")
    assert any(evt.name == "tool.succeeded" for evt in runtime.event_bus.history)
    assert any(evt.name == "llm.succeeded" for evt in runtime.event_bus.history)

    state = await runtime.session_manager.get_state("tool-sess")
    assert len(state["memory_buffer"]) == 1
    assert state["memory_buffer"][0]["tool_results"]


@pytest.mark.asyncio
async def test_window_buffer_trims_by_window_size():
    config = load_config_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "assistant",
                    "name": "window-agent",
                    "memory": {"type": "window_buffer", "config": {"window_size": 2}},
                    "pattern": {"type": "react"},
                    "llm": {"provider": "mock"},
                    "tools": [],
                    "runtime": {"max_steps": 8, "step_timeout_ms": 1000},
                }
            ],
        }
    )
    runtime = Runtime(config)

    result1 = await runtime.run(agent_id="assistant", session_id="w", input_text="first")
    result2 = await runtime.run(agent_id="assistant", session_id="w", input_text="second")
    result3 = await runtime.run(agent_id="assistant", session_id="w", input_text="third")

    assert "history=0" in result1
    assert "history=1" in result2
    assert "history=2" in result3

    state = await runtime.session_manager.get_state("w")
    assert len(state["memory_buffer"]) == 2
    assert [row["input"] for row in state["memory_buffer"]] == ["second", "third"]


def test_buffer_memory_warns_on_unknown_config_keys(caplog):
    import logging

    from openagents.plugins.builtin.memory.buffer import BufferMemory

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        plugin = BufferMemory({"state_key": "ok", "totally_unknown": 1})

    assert plugin.cfg.state_key == "ok"
    assert any("unknown config keys" in r.message and "totally_unknown" in r.message for r in caplog.records)


def test_window_buffer_memory_warns_on_unknown_config_keys(caplog):
    import logging

    from openagents.plugins.builtin.memory.window_buffer import WindowBufferMemory

    with caplog.at_level(logging.WARNING, logger="openagents.plugins.builtin.memory.window_buffer"):
        plugin = WindowBufferMemory({"window_size": 5, "totally_unknown": 1})

    assert plugin.window_size() == 5
    assert any("unknown config keys" in r.message and "totally_unknown" in r.message for r in caplog.records)
