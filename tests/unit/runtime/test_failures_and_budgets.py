from __future__ import annotations

import logging

import pytest

from openagents.config.loader import load_config_dict
from openagents.interfaces.runtime import RunBudget, RunRequest
from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
from openagents.runtime.runtime import Runtime


def _payload(memory_impl: str, pattern_impl: str, *, on_error: str = "continue") -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "runtime-test-agent",
                "memory": {"impl": memory_impl, "on_error": on_error},
                "pattern": {"impl": pattern_impl},
                "llm": {"provider": "mock"},
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


@pytest.mark.asyncio
async def test_async_event_bus_logs_and_continues_after_handler_failure(caplog):
    bus = AsyncEventBus()
    seen: list[str] = []

    async def _broken(event):
        raise RuntimeError("boom")

    async def _healthy(event):
        seen.append(event.name)

    bus.subscribe("run.requested", _broken)
    bus.subscribe("run.requested", _healthy)

    caplog.set_level(logging.ERROR, logger="openagents")
    await bus.emit("run.requested", agent_id="assistant")

    assert seen == ["run.requested"]
    assert "handler failed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_runtime_logs_memory_failure_when_continue(caplog):
    config = load_config_dict(
        _payload(
            "tests.fixtures.runtime_plugins.FailingInjectMemory",
            "tests.fixtures.runtime_plugins.FinalPattern",
            on_error="continue",
        )
    )
    runtime = Runtime(config)

    caplog.set_level(logging.WARNING, logger="openagents")
    result = await runtime.run(
        agent_id="assistant",
        session_id="memory-log-session",
        input_text="hello",
    )

    assert result == "injected=False"
    assert "on_error=continue" in caplog.text


@pytest.mark.asyncio
async def test_runtime_enforces_max_tool_calls_budget():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.TwoToolCallsPattern",
    )
    payload["agents"][0]["tools"] = [
        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
    ]
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="tool-budget-session",
            input_text="hello",
            budget=RunBudget(max_tool_calls=1),
        )
    )

    assert result.stop_reason == "max_steps"
    assert "Tool call limit" in (result.error or "")


@pytest.mark.asyncio
async def test_runtime_enforces_duration_budget():
    payload = _payload(
        "tests.fixtures.runtime_plugins.InjectWritebackMemory",
        "tests.fixtures.runtime_plugins.SlowFinalPattern",
    )
    payload["agents"][0]["pattern"]["config"] = {"delay": 0.05}
    config = load_config_dict(payload)
    runtime = Runtime(config)

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="duration-budget-session",
            input_text="hello",
            budget=RunBudget(max_duration_ms=5),
        )
    )

    assert result.stop_reason == "budget_exhausted"
    assert "duration limit" in (result.error or "").lower()
