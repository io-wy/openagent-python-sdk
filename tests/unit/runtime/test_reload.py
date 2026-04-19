from __future__ import annotations

import json

import pytest

import openagents.plugins.loader as plugin_loader
import openagents.runtime.runtime as runtime_module
from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import ConfigError
from openagents.runtime.runtime import Runtime


def _payload(*, name: str = "assistant", llm: dict | None = None) -> dict:
    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": name,
                "memory": {"type": "buffer", "on_error": "continue"},
                "pattern": {"type": "react"},
                "tools": [],
                "runtime": {
                    "max_steps": 8,
                    "step_timeout_ms": 1000,
                    "session_queue_size": 100,
                    "event_queue_size": 100,
                },
            }
        ],
    }
    if llm is not None:
        payload["agents"][0]["llm"] = llm
    return payload


@pytest.mark.asyncio
async def test_default_runtime_uses_preloaded_session_plugins(monkeypatch):
    config = load_config_dict(_payload(llm={"provider": "mock"}))
    runtime = Runtime(config)
    calls: list[str] = []
    original = plugin_loader.load_agent_plugins

    def wrapped(agent):
        calls.append(agent.id)
        return original(agent)

    monkeypatch.setattr(plugin_loader, "load_agent_plugins", wrapped)
    monkeypatch.setattr(runtime_module, "load_agent_plugins", wrapped)

    result = await runtime.run(agent_id="assistant", session_id="s1", input_text="hello")

    assert result.startswith("Echo:")
    assert calls == ["assistant"]
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_without_llm_uses_pattern_fallback():
    runtime = Runtime(load_config_dict(_payload(llm=None)))

    result = await runtime.run(agent_id="assistant", session_id="fallback", input_text="hello")

    assert result.startswith("Echo: hello")
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reload_updates_agents_and_invalidates_cache(tmp_path):
    config_path = tmp_path / "agent.json"
    initial = _payload(name="before", llm={"provider": "mock"})
    config_path.write_text(json.dumps(initial), encoding="utf-8")
    runtime = Runtime.from_config(config_path)

    runtime._runtime._llm_clients["assistant"] = object()

    updated = _payload(name="after", llm={"provider": "mock", "model": "mock-react-v2"})
    updated["agents"].append(
        {
            "id": "helper",
            "name": "helper",
            "memory": {"type": "buffer", "on_error": "continue"},
            "pattern": {"type": "react"},
            "llm": {"provider": "mock"},
            "tools": [],
            "runtime": {
                "max_steps": 8,
                "step_timeout_ms": 1000,
                "session_queue_size": 100,
                "event_queue_size": 100,
            },
        }
    )
    config_path.write_text(json.dumps(updated), encoding="utf-8")

    await runtime.reload()

    agents = await runtime.list_agents()
    assert {"id": "assistant", "name": "after"} in agents
    assert {"id": "helper", "name": "helper"} in agents
    assert "assistant" not in runtime._runtime._llm_clients

    event = runtime.event_bus.history[-1]
    assert event.name == "config.reloaded"
    assert event.payload["changed_agents"] == ["assistant", "helper"]
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_reload_rejects_top_level_component_changes(tmp_path):
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(_payload(llm={"provider": "mock"})), encoding="utf-8")
    runtime = Runtime.from_config(config_path)

    updated = _payload(llm={"provider": "mock"})
    updated["events"] = {"type": "async", "config": {"max_history": 5}}
    config_path.write_text(json.dumps(updated), encoding="utf-8")

    with pytest.raises(ConfigError, match="top-level runtime/session/events"):
        await runtime.reload()

    await runtime.close()
