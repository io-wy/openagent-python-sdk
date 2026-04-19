from __future__ import annotations

import asyncio
import time
from collections import Counter

import pytest

from openagents.config.loader import load_config_dict
from openagents.runtime.runtime import Runtime


def _payload(*, delay: float, max_items: int = 1000) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "pressure-agent",
                "memory": {"type": "buffer", "config": {"max_items": max_items}},
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.SlowFinalPattern",
                    "config": {"delay": delay},
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 6,
                    "step_timeout_ms": 5000,
                    "session_queue_size": 5000,
                    "event_queue_size": 10000,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_same_session_pressure_no_loss_and_serial_timing():
    task_count = 30
    delay = 0.01
    runtime = Runtime(load_config_dict(_payload(delay=delay)))

    inputs = [f"msg-{i}" for i in range(task_count)]
    start = time.perf_counter()
    await asyncio.gather(
        *[
            runtime.run(agent_id="assistant", session_id="shared", input_text=msg)
            for msg in inputs
        ]
    )
    elapsed = time.perf_counter() - start

    # Same session must serialize.
    assert elapsed >= task_count * delay * 0.75

    state = await runtime.session_manager.get_state("shared")
    rows = state.get("memory_buffer", [])
    assert len(rows) == task_count
    assert Counter(row["input"] for row in rows) == Counter(inputs)


@pytest.mark.asyncio
async def test_cross_session_pressure_runs_concurrently():
    task_count = 20
    delay = 0.02
    runtime = Runtime(load_config_dict(_payload(delay=delay)))

    start = time.perf_counter()
    await asyncio.gather(
        *[
            runtime.run(
                agent_id="assistant",
                session_id=f"s-{i}",
                input_text=f"payload-{i}",
            )
            for i in range(task_count)
        ]
    )
    elapsed = time.perf_counter() - start

    serial_baseline = task_count * delay
    # Cross session should run substantially faster than fully serialized baseline.
    assert elapsed < serial_baseline * 0.7

