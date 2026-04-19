"""Tests for Runtime.run_stream event-bus projection."""

from __future__ import annotations

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
                "name": "stream-test",
                "memory": {
                    "impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"
                },
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
async def test_run_stream_yields_run_finished_with_result():
    runtime = Runtime(load_config_dict(_build_config(["hello"])))
    request = RunRequest(agent_id="assistant", session_id="s1", input_text="hi")
    chunks = []
    async for chunk in runtime.run_stream(request=request):
        chunks.append(chunk)
    assert chunks, "run_stream yielded zero chunks"
    terminal = chunks[-1]
    assert terminal.kind is RunStreamChunkKind.RUN_FINISHED
    assert terminal.result is not None
    assert terminal.result.run_id == request.run_id
    # Sequence numbers are monotonically increasing.
    seqs = [c.sequence for c in chunks]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_run_stream_sets_runtime_streaming_hint():
    """The streaming flag must be attached to context_hints before the run executes."""
    runtime = Runtime(load_config_dict(_build_config(["x"])))
    request = RunRequest(agent_id="assistant", session_id="s2", input_text="hi")
    async for _ in runtime.run_stream(request=request):
        pass
    assert request.context_hints.get("__runtime_streaming__") is True


@pytest.mark.asyncio
async def test_run_stream_terminal_result_matches_run_detailed():
    """Streaming and non-streaming runs should produce equivalent final_output for the same config."""
    runtime_a = Runtime(load_config_dict(_build_config(["same-output"])))
    runtime_b = Runtime(load_config_dict(_build_config(["same-output"])))
    request_a = RunRequest(agent_id="assistant", session_id="sa", input_text="x")
    request_b = RunRequest(agent_id="assistant", session_id="sb", input_text="x")

    detailed = await runtime_a.run_detailed(request=request_a)

    streamed_result = None
    async for chunk in runtime_b.run_stream(request=request_b):
        if chunk.kind is RunStreamChunkKind.RUN_FINISHED:
            streamed_result = chunk.result

    assert streamed_result is not None
    assert detailed.final_output == streamed_result.final_output
    assert detailed.stop_reason == streamed_result.stop_reason
