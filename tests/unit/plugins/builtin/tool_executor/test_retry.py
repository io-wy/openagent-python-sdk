from __future__ import annotations

import asyncio
import random
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openagents.errors.exceptions import (
    OpenAgentsError,
    PermanentToolError,
    RetryableToolError,
    ToolRateLimitError,
    ToolTimeoutError,
    ToolValidationError,
)
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
    ToolExecutorPlugin,
)
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor
from openagents.plugins.registry import get_builtin_plugin_class


class _FakeTool:
    pass


class _ScriptedExecutor(ToolExecutorPlugin):
    def __init__(self, results: list[ToolExecutionResult]):
        super().__init__(config={})
        self._results = list(results)
        self.calls = 0

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.calls += 1
        return self._results.pop(0)

    async def execute_stream(self, request: ToolExecutionRequest):
        yield {"type": "stream-passthrough", "calls": self.calls}


def _req() -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_id="demo",
        tool=_FakeTool(),
        params={},
        execution_spec=ToolExecutionSpec(),
    )


def _ok() -> ToolExecutionResult:
    return ToolExecutionResult(tool_id="demo", success=True, data="ok")


def _retryable() -> ToolExecutionResult:
    exc = RetryableToolError("transient", tool_name="demo")
    return ToolExecutionResult(tool_id="demo", success=False, error=str(exc), exception=exc)


def _timeout() -> ToolExecutionResult:
    exc = ToolTimeoutError("slow", tool_name="demo")
    return ToolExecutionResult(tool_id="demo", success=False, error=str(exc), exception=exc)


def _permanent() -> ToolExecutionResult:
    exc = PermanentToolError("nope", tool_name="demo")
    return ToolExecutionResult(tool_id="demo", success=False, error=str(exc), exception=exc)


def _make(inner: _ScriptedExecutor, **overrides: Any) -> RetryToolExecutor:
    cfg = {"inner": {"type": "safe"}}
    cfg.update(overrides)
    retry = RetryToolExecutor(config={"max_attempts": 3, "initial_delay_ms": 1, "max_delay_ms": 2, **cfg})
    retry._inner = inner
    return retry


@pytest.mark.asyncio
async def test_first_call_success_no_retry():
    inner = _ScriptedExecutor([_ok()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is True
    assert inner.calls == 1
    assert result.metadata.get("retry_attempts", 1) == 1


@pytest.mark.asyncio
async def test_retryable_then_success():
    inner = _ScriptedExecutor([_retryable(), _retryable(), _ok()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is True
    assert inner.calls == 3
    assert result.metadata["retry_attempts"] == 3


@pytest.mark.asyncio
async def test_retryable_exhaustion_returns_failure():
    inner = _ScriptedExecutor([_retryable(), _retryable(), _retryable()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is False
    assert inner.calls == 3
    assert result.metadata["retry_attempts"] == 3
    assert len(result.metadata["retry_delays_ms"]) == 2
    assert isinstance(result.exception, RetryableToolError)


@pytest.mark.asyncio
async def test_timeout_retries_because_retryable_true():
    # ToolTimeoutError.retryable = True, so it is retried without any extra config
    inner = _ScriptedExecutor([_timeout(), _ok()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is True
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_permanent_error_not_retried_by_attribute():
    # PermanentToolError.retryable = False — verified via attribute path
    inner = _ScriptedExecutor([_permanent(), _ok()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is False
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_permanent_error_not_retried():
    inner = _ScriptedExecutor([_permanent()])
    retry = _make(inner)
    result = await retry.execute(_req())
    assert result.success is False
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_execute_stream_passthrough_no_retry():
    inner = _ScriptedExecutor([])
    retry = _make(inner)
    chunks = [c async for c in retry.execute_stream(_req())]
    assert chunks == [{"type": "stream-passthrough", "calls": 0}]


@pytest.mark.asyncio
async def test_cancellation_during_backoff_propagates():
    inner = _ScriptedExecutor([_retryable(), _retryable(), _ok()])
    retry = RetryToolExecutor(
        config={
            "max_attempts": 3,
            "initial_delay_ms": 1_000_000,
            "max_delay_ms": 1_000_000,
            "inner": {"type": "safe"},
        }
    )
    retry._inner = inner

    async def run():
        return await retry.execute(_req())

    task = asyncio.create_task(run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_registered_as_builtin():
    assert get_builtin_plugin_class("tool_executor", "retry") is RetryToolExecutor


def test_attribute_driven_classification_replaces_name_list():
    """Verify that the old _retry_on name-set is gone and classification uses .retryable."""
    from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor

    exec_plugin = RetryToolExecutor()
    # New interface: attribute-based
    assert not hasattr(exec_plugin, "_retry_on"), "_retry_on class-name set must be removed"
    assert not hasattr(exec_plugin, "_retry_on_timeout"), "_retry_on_timeout flag must be removed"
    # Spot-check the helper
    from openagents.errors.exceptions import RetryableToolError, ToolValidationError

    assert exec_plugin._should_retry(RetryableToolError("t", tool_name="x")) is True
    assert exec_plugin._should_retry(ToolValidationError("bad", tool_name="x")) is False


# ---------------------------------------------------------------------------
# New tests for Task 8 (attribute-driven + jitter + retry_after_ms)
# ---------------------------------------------------------------------------


class _CustomRetryable(OpenAgentsError):
    code = "user.custom_retryable"
    retryable = True


def _request(tool_id: str = "x") -> ToolExecutionRequest:
    return ToolExecutionRequest(tool_id=tool_id, tool=object(), params={}, execution_spec=ToolExecutionSpec())


@pytest.mark.asyncio
async def test_retry_uses_retryable_attribute_not_class_name_list(monkeypatch):
    """Attribute-driven classification catches user subclasses without configuration."""
    exc = _CustomRetryable("transient")
    inner = AsyncMock()
    inner.execute.side_effect = [
        ToolExecutionResult(tool_id="x", success=False, exception=exc, error="t"),
        ToolExecutionResult(tool_id="x", success=True, data="ok"),
    ]
    executor = RetryToolExecutor(config={"inner": {"type": "safe"}, "max_attempts": 2, "jitter": "none"})
    executor._inner = inner
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    result = await executor.execute(_request("x"))
    assert result.success is True
    assert result.metadata["retry_attempts"] == 2


@pytest.mark.asyncio
async def test_retry_skips_permanent_errors_by_attribute():
    exc = ToolValidationError("bad input", tool_name="x")  # retryable=False
    inner = AsyncMock()
    inner.execute.return_value = ToolExecutionResult(tool_id="x", success=False, exception=exc, error="bad input")
    executor = RetryToolExecutor(config={"inner": {"type": "safe"}, "max_attempts": 3, "jitter": "none"})
    executor._inner = inner
    result = await executor.execute(_request("x"))
    assert result.success is False
    assert inner.execute.await_count == 1  # no retry


@pytest.mark.asyncio
async def test_retry_uses_retry_after_ms_as_sleep_floor(monkeypatch):
    exc = ToolRateLimitError("429", tool_name="api", retry_after_ms=2000)
    inner = AsyncMock()
    inner.execute.side_effect = [
        ToolExecutionResult(tool_id="api", success=False, exception=exc, error="429"),
        ToolExecutionResult(tool_id="api", success=True, data="ok"),
    ]
    captured: list[float] = []

    async def _sleep(s):
        captured.append(s)

    monkeypatch.setattr("asyncio.sleep", _sleep)
    executor = RetryToolExecutor(
        config={
            "inner": {"type": "safe"},
            "max_attempts": 2,
            "initial_delay_ms": 100,
            "max_delay_ms": 5000,
            "jitter": "none",
        }
    )
    executor._inner = inner
    result = await executor.execute(_request("api"))
    assert result.success is True
    # initial backoff would be 100ms but retry_after_ms=2000 raises the floor
    assert captured[0] == pytest.approx(2.0)


def test_jitter_equal_reduces_delay_to_half_plus_random(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 0)  # lower bound of [0, delay/2]
    executor = RetryToolExecutor(
        config={
            "inner": {"type": "safe"},
            "initial_delay_ms": 1000,
            "backoff_multiplier": 1.0,
            "max_delay_ms": 5000,
            "jitter": "equal",
        }
    )
    delay_ms = executor._delay_for(0, exc=None)
    assert delay_ms == 500  # 1000 // 2 + 0
