"""Integration-style tests for the validation retry loop driven by the runtime.

The runtime must, after ``pattern.execute()`` returns raw output, call
``pattern.finalize(raw, output_type)``. On ``ModelRetryError`` it must retry up
to ``RunBudget.max_validation_retries`` times (re-entering ``execute()`` each
time so the pattern can pick up the queued correction via
``_inject_validation_correction``); on exhaustion it must return a
``RunResult`` with ``stop_reason=FAILED`` and ``exception=OutputValidationError``.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import OutputValidationError
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime


class UserProfile(BaseModel):
    name: str
    age: int


def _build_config(responses: list) -> dict:
    """Build an AppConfig dict that queues raw outputs on a fixture pattern.

    The fixture pattern returns one item from ``responses`` per ``execute()``
    call. Each item is fed through ``PatternPlugin.finalize(raw, UserProfile)``
    by the runtime, so strings must be JSON-parseable dicts that (sometimes)
    satisfy ``UserProfile``.
    """
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "validation-retry-agent",
                "memory": {
                    "impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"
                },
                "pattern": {
                    "impl": "tests.fixtures.runtime_plugins.QueuedRawOutputPattern",
                    "config": {
                        # Raw outputs — fed to finalize() one at a time. Use dicts
                        # (not JSON strings) so pydantic's model_validate accepts them.
                        "responses": responses,
                    },
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
async def test_retry_succeeds_on_third_attempt():
    responses = [
        {"name": "a"},                    # attempt 1: missing age
        {"name": "a", "age": "not-int"},  # attempt 2: age wrong type
        {"name": "ada", "age": 33},       # attempt 3: valid
    ]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s1",
        input_text="give me a user profile",
        output_type=UserProfile,
        budget=RunBudget(max_validation_retries=3),
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    assert isinstance(result.final_output, UserProfile)
    assert result.final_output.name == "ada"
    assert result.final_output.age == 33


@pytest.mark.asyncio
async def test_retry_exhausts_and_returns_output_validation_error():
    # All responses invalid; retry budget allows only 2 retries after the
    # first failure, so the 3rd failure (attempt count = 3) crosses the limit.
    responses = [
        {"name": "a"},
        {"bad": "data"},
        {"age": 1},
        "not even json",
    ]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s2",
        input_text="give me a user profile",
        output_type=UserProfile,
        budget=RunBudget(max_validation_retries=2),
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    assert isinstance(result.exception, OutputValidationError)
    assert result.final_output is None


@pytest.mark.asyncio
async def test_no_output_type_skips_validation():
    """When output_type is None, finalize passes raw through unchanged."""
    responses = ["hello world"]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s3",
        input_text="no validation",
        # output_type omitted → None
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    assert result.final_output == "hello world"


@pytest.mark.asyncio
async def test_validation_retry_event_emitted_per_retry():
    """`validation.retry` must be emitted on each retry (not on final success or exhaustion)."""
    responses = [
        {"name": "a"},                     # fail
        {"name": "b"},                     # fail
        {"name": "zoe", "age": 7},         # success
    ]
    runtime = Runtime(load_config_dict(_build_config(responses)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s4",
        input_text="retry me",
        output_type=UserProfile,
        budget=RunBudget(max_validation_retries=3),
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    retry_events = [e for e in runtime.event_bus.history if e.name == "validation.retry"]
    # Two failed attempts → two retry events before the third succeeds.
    assert len(retry_events) == 2
    attempts = [e.payload.get("attempt") for e in retry_events]
    assert attempts == [1, 2]
