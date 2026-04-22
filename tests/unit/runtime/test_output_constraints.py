from __future__ import annotations

import pytest

from openagents.config.loader import load_config_dict
from openagents.runtime.runtime import Runtime


def _payload(
    *,
    pattern_impl: str,
    pattern_config: dict | None = None,
    max_steps: int = 4,
    step_timeout_ms: int = 1000,
    include_search_tool: bool = False,
) -> dict:
    tools = [{"id": "search", "type": "builtin_search"}] if include_search_tool else []
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "constraint-agent",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {"impl": pattern_impl, "config": pattern_config or {}},
                "llm": {"provider": "mock"},
                "tools": tools,
                "runtime": {
                    "max_steps": max_steps,
                    "step_timeout_ms": step_timeout_ms,
                    "session_queue_size": 100,
                    "event_queue_size": 100,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_rejects_non_dict_action():
    runtime = Runtime(load_config_dict(_payload(pattern_impl="tests.fixtures.runtime_plugins.NonDictActionPattern")))
    with pytest.raises(RuntimeError, match="Pattern action must be dict"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="x")
    assert any(evt.name == "run.failed" for evt in runtime.event_bus.history)


@pytest.mark.asyncio
async def test_rejects_unknown_action_type():
    runtime = Runtime(load_config_dict(_payload(pattern_impl="tests.fixtures.runtime_plugins.UnknownTypePattern")))
    with pytest.raises(RuntimeError, match="Unsupported pattern action type"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="x")


@pytest.mark.asyncio
async def test_rejects_tool_call_without_tool_id():
    runtime = Runtime(
        load_config_dict(
            _payload(
                pattern_impl="tests.fixtures.runtime_plugins.MissingToolCallFieldPattern",
                include_search_tool=True,
            )
        )
    )
    with pytest.raises(RuntimeError, match="must include non-empty 'tool' or 'tool_id'"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="x")


@pytest.mark.asyncio
async def test_rejects_tool_call_with_non_object_params():
    runtime = Runtime(
        load_config_dict(
            _payload(
                pattern_impl="tests.fixtures.runtime_plugins.InvalidToolCallParamsPattern",
                include_search_tool=True,
            )
        )
    )
    with pytest.raises(RuntimeError, match="params' must be an object"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="x")


@pytest.mark.asyncio
async def test_step_timeout_is_enforced():
    from openagents.interfaces.runtime import RunRequest

    runtime = Runtime(
        load_config_dict(
            _payload(
                pattern_impl="tests.fixtures.runtime_plugins.SlowContinuePattern",
                pattern_config={"delay": 0.05},
                step_timeout_ms=10,
            )
        )
    )
    result = await runtime.run_detailed(request=RunRequest(agent_id="assistant", session_id="s1", input_text="x"))
    assert result.error_details is not None
    assert "Pattern step timed out after 10ms at step 0" in result.error_details.message


@pytest.mark.asyncio
async def test_max_steps_is_enforced():
    runtime = Runtime(
        load_config_dict(
            _payload(
                pattern_impl="tests.fixtures.runtime_plugins.ContinueForeverPattern",
                max_steps=2,
            )
        )
    )
    with pytest.raises(RuntimeError, match=r"Pattern exceeded max_steps \(2\)"):
        await runtime.run(agent_id="assistant", session_id="s1", input_text="x")


@pytest.mark.asyncio
async def test_session_lock_releases_after_failure():
    runtime = Runtime(
        load_config_dict(
            _payload(
                pattern_impl="tests.fixtures.runtime_plugins.FailOnceThenFinalPattern",
            )
        )
    )

    with pytest.raises(RuntimeError, match="pattern fail once"):
        await runtime.run(agent_id="assistant", session_id="same", input_text="x")

    result = await runtime.run(agent_id="assistant", session_id="same", input_text="x")
    assert result == "recovered"
