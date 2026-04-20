"""End-to-end integration test for structured output via RunRequest.output_type."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from openagents.config.loader import load_config_dict
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime


class Answer(BaseModel):
    city: str
    population: int


def _build_config(responses: list) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "structured-output-e2e",
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
async def test_structured_output_end_to_end_returns_typed_model():
    responses = [{"city": "paris", "population": 2_000_000}]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="e2e-1",
        input_text="city info",
        output_type=Answer,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    assert isinstance(result.final_output, Answer)
    assert result.final_output.city == "paris"
    assert result.final_output.population == 2_000_000


@pytest.mark.asyncio
async def test_structured_output_coerces_via_pydantic():
    # Pydantic v2 coerces digit-strings to int by default.
    responses = [{"city": "london", "population": "9000000"}]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="e2e-2",
        input_text="city info",
        output_type=Answer,
        budget=RunBudget(max_validation_retries=1),
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    assert isinstance(result.final_output, Answer)
    assert result.final_output.population == 9_000_000
