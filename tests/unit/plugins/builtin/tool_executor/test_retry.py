from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openagents.errors.exceptions import (
    PermanentToolError,
    RetryableToolError,
    ToolTimeoutError,
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
        super().__init__(config={}, capabilities=set())
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
async def test_timeout_retries_when_flag_true():
    inner = _ScriptedExecutor([_timeout(), _ok()])
    retry = _make(inner, retry_on_timeout=True)
    result = await retry.execute(_req())
    assert result.success is True
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_timeout_not_retried_when_flag_false():
    inner = _ScriptedExecutor([_timeout(), _ok()])
    retry = RetryToolExecutor(config={
        "max_attempts": 3, "initial_delay_ms": 1, "max_delay_ms": 2,
        "retry_on_timeout": False,
        "retry_on": ["RetryableToolError"],
        "inner": {"type": "safe"},
    })
    retry._inner = inner
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
    retry = RetryToolExecutor(config={
        "max_attempts": 3, "initial_delay_ms": 1_000_000, "max_delay_ms": 1_000_000,
        "inner": {"type": "safe"},
    })
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


def test_default_retry_on_includes_ratelimit_and_unavailable():
    from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor

    exec_plugin = RetryToolExecutor()
    defaults = exec_plugin._retry_on
    assert "RetryableToolError" in defaults
    assert "ToolTimeoutError" in defaults
    assert "ToolRateLimitError" in defaults
    assert "ToolUnavailableError" in defaults
    # Non-retryable types are NOT in the default list:
    assert "ToolValidationError" not in defaults
    assert "ToolAuthError" not in defaults
    assert "ToolCancelledError" not in defaults
