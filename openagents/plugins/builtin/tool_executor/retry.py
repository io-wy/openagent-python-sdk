"""Retry wrapper tool executor."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from openagents.errors.exceptions import ToolError, ToolTimeoutError
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)


class RetryToolExecutor(ToolExecutorPlugin):
    """Wraps another ToolExecutor and retries on classified errors with exponential backoff.

    ``execute_stream`` does not retry; it delegates transparently.
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_attempts: int = 3
        initial_delay_ms: int = 200
        backoff_multiplier: float = 2.0
        max_delay_ms: int = 5_000
        retry_on_timeout: bool = True
        retry_on: list[str] = Field(default_factory=lambda: ["RetryableToolError", "ToolTimeoutError"])

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        cfg = self.Config.model_validate(self.config)
        self._max_attempts = max(1, cfg.max_attempts)
        self._initial_delay_ms = max(0, cfg.initial_delay_ms)
        self._backoff = max(1.0, cfg.backoff_multiplier)
        self._max_delay_ms = max(self._initial_delay_ms, cfg.max_delay_ms)
        self._retry_on_timeout = cfg.retry_on_timeout
        self._retry_on = set(cfg.retry_on)
        self._inner = self._load_inner(cfg.inner)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import ToolExecutorRef
        from openagents.plugins.loader import _load_plugin

        return _load_plugin("tool_executor", ToolExecutorRef(**ref), required_methods=("execute", "execute_stream"))

    def _should_retry(self, exc: Exception | None) -> bool:
        if exc is None:
            return False
        name = type(exc).__name__
        if name in self._retry_on:
            return True
        if self._retry_on_timeout and isinstance(exc, ToolTimeoutError):
            return True
        return False

    def _delay_for(self, attempt: int) -> int:
        delay = self._initial_delay_ms * (self._backoff ** attempt)
        return int(min(self._max_delay_ms, delay))

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        delays: list[int] = []
        reasons: list[str] = []
        last_result: ToolExecutionResult | None = None
        for attempt in range(self._max_attempts):
            result = await self._inner.execute(request)
            if result.success or not self._should_retry(result.exception):
                metadata = dict(result.metadata or {})
                metadata.setdefault("retry_attempts", attempt + 1)
                if delays:
                    metadata["retry_delays_ms"] = delays
                    metadata["retry_reason"] = reasons
                return result.model_copy(update={"metadata": metadata})
            last_result = result
            if attempt + 1 >= self._max_attempts:
                break
            delay_ms = self._delay_for(attempt)
            delays.append(delay_ms)
            reasons.append(type(result.exception).__name__ if result.exception else "unknown")
            await asyncio.sleep(delay_ms / 1000)
        assert last_result is not None
        metadata = dict(last_result.metadata or {})
        metadata["retry_attempts"] = self._max_attempts
        metadata["retry_delays_ms"] = delays
        metadata["retry_reason"] = reasons
        return last_result.model_copy(update={"metadata": metadata})

    async def execute_stream(self, request: ToolExecutionRequest):
        async for chunk in self._inner.execute_stream(request):
            yield chunk
