"""Tests for the synchronous streaming helpers in openagents.runtime.sync."""

from __future__ import annotations

from openagents.interfaces.runtime import RunRequest, RunStreamChunkKind
from openagents.runtime.sync import stream_agent_with_dict


def _build_config(responses: list) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "sync-stream",
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


def test_stream_agent_with_dict_yields_terminal_chunk():
    request = RunRequest(agent_id="assistant", session_id="s", input_text="hi")
    chunks = list(stream_agent_with_dict(_build_config(["ok"]), request=request))
    assert chunks, "sync stream yielded no chunks"
    assert chunks[-1].kind is RunStreamChunkKind.RUN_FINISHED
    assert chunks[-1].result is not None
    assert chunks[-1].result.run_id == request.run_id
