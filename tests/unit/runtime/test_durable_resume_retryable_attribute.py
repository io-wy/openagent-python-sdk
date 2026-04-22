"""Durable resume catches by exc.retryable, not by a hardcoded tuple.

Asserts that:
- A user-defined OpenAgentsError subclass with retryable=True participates
  in durable resume automatically (no monkey-patching needed).
- A user-defined OpenAgentsError subclass with retryable=False does NOT
  trigger resume — it propagates as a permanent failure.
"""

from __future__ import annotations

from typing import Any

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import OpenAgentsError
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# User-defined error subclasses that are NOT in the old RETRYABLE_RUN_ERRORS
# ---------------------------------------------------------------------------


class _UserRetryableError(OpenAgentsError):
    code = "user.my_retryable"
    retryable = True


class _UserPermanentError(OpenAgentsError):
    code = "user.my_permanent"
    retryable = False


# ---------------------------------------------------------------------------
# Minimal scripted pattern — raises user errors at configurable attempt index
# ---------------------------------------------------------------------------


class _RetryableAttrPattern(PatternPlugin):
    """Pattern that emits one llm step then raises a given exception on the
    first execute(); on the second call it returns 'done'.

    ``exc_to_raise`` is injected via config['exc_class_path'] (dotted name).
    The exception instance is stored in the module-level registry so the
    pattern can reach it without import machinery.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        from openagents.interfaces.capabilities import PATTERN_EXECUTE

        super().__init__(config=config or {}, capabilities={PATTERN_EXECUTE})
        self._attempt = 0

    async def execute(self) -> Any:
        attempt = self._attempt
        self._attempt += 1
        if attempt == 0:
            # Emit a step so a checkpoint is written before the error.
            await self.emit("llm.called", model="mock")
            await self.emit("llm.succeeded", model="mock")
            exc_key = self.config.get("exc_key", "")
            exc = _EXC_REGISTRY.get(exc_key)
            if exc is not None:
                raise exc
            return "no-exc"
        return "done"


# Module-level registry so test functions can inject exception instances
# without needing import gymnastics in the config dict.
_EXC_REGISTRY: dict[str, BaseException] = {}


def _build_config(exc_key: str) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "retryable-attr-test-agent",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {
                    "impl": f"{_RetryableAttrPattern.__module__}.{_RetryableAttrPattern.__name__}",
                    "config": {"exc_key": exc_key},
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 20,
                    "step_timeout_ms": 2000,
                    "session_queue_size": 100,
                    "event_queue_size": 100,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Test: user-defined retryable=True error triggers durable resume
# ---------------------------------------------------------------------------


async def test_user_retryable_error_participates_in_durable_resume():
    """_UserRetryableError (retryable=True) should trigger resume and complete."""
    exc_key = "user_retryable"
    _EXC_REGISTRY[exc_key] = _UserRetryableError("transient failure")

    runtime = Runtime(load_config_dict(_build_config(exc_key)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-user-retryable",
        input_text="hello",
        durable=True,
        budget=RunBudget(max_resume_attempts=3),
    )
    result = await runtime.run_detailed(request=request)

    assert result.stop_reason == StopReason.COMPLETED, (
        f"Expected completed after resume, got {result.stop_reason}: {result.error_details}"
    )
    event_names = [e.name for e in runtime.event_bus.history]
    assert "run.resume_attempted" in event_names, "resume should have been attempted"
    assert "run.resume_succeeded" in event_names, "resume should have succeeded"
    assert "run.resume_exhausted" not in event_names


# ---------------------------------------------------------------------------
# Test: user-defined retryable=False error does NOT trigger resume
# ---------------------------------------------------------------------------


async def test_user_permanent_error_does_not_trigger_resume():
    """_UserPermanentError (retryable=False) must propagate without resume."""
    exc_key = "user_permanent"
    _EXC_REGISTRY[exc_key] = _UserPermanentError("hard failure")

    runtime = Runtime(load_config_dict(_build_config(exc_key)))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-user-permanent",
        input_text="hello",
        durable=True,
        budget=RunBudget(max_resume_attempts=3),
    )
    result = await runtime.run_detailed(request=request)

    assert result.stop_reason == StopReason.FAILED, f"Expected failed (permanent), got {result.stop_reason}"
    assert result.error_details is not None
    assert result.error_details.code == "user.my_permanent", f"Wrong error code: {result.error_details.code}"
    event_names = [e.name for e in runtime.event_bus.history]
    assert "run.resume_attempted" not in event_names, "permanent error must NOT trigger resume"
