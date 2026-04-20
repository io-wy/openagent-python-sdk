"""End-to-end integration tests for Runtime.run_stream."""

from __future__ import annotations

import asyncio

import pytest

from openagents.config.loader import load_config_dict
from openagents.interfaces.runtime import RunRequest, RunStreamChunkKind
from openagents.runtime.runtime import Runtime


def _build_config(responses: list) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "stream-e2e",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.QueuedRawOutputPattern",
                    "config": {"responses": responses},
                },
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
async def test_end_to_end_stream_matches_run_detailed():
    runtime_a = Runtime(load_config_dict(_build_config(["hello"])))
    runtime_b = Runtime(load_config_dict(_build_config(["hello"])))

    detailed = await runtime_a.run_detailed(request=RunRequest(agent_id="assistant", session_id="sa", input_text="hi"))

    streamed = None
    async for chunk in runtime_b.run_stream(request=RunRequest(agent_id="assistant", session_id="sb", input_text="hi")):
        if chunk.kind is RunStreamChunkKind.RUN_FINISHED:
            streamed = chunk.result

    assert streamed is not None
    assert detailed.final_output == streamed.final_output
    assert detailed.stop_reason == streamed.stop_reason


@pytest.mark.asyncio
async def test_cancel_mid_stream_releases_session_lock():
    """Consumer abandons iteration; subsequent runs on the same session still work."""
    runtime = Runtime(load_config_dict(_build_config(["a", "b"])))
    request = RunRequest(agent_id="assistant", session_id="s-cancel", input_text="first")

    iterator = runtime.run_stream(request=request).__aiter__()
    # Consume one chunk then close early.
    first_chunk = await iterator.__anext__()
    assert first_chunk is not None
    await iterator.aclose()

    # Let cancellation propagate.
    await asyncio.sleep(0.05)

    # A fresh run on the same session should not deadlock.
    second = await runtime.run_detailed(
        request=RunRequest(agent_id="assistant", session_id="s-cancel", input_text="second")
    )
    assert second is not None
    assert second.run_id != request.run_id
