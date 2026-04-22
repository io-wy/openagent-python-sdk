"""Retry wrapper tool executor."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Literal

from pydantic import BaseModel, Field

from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)


class RetryToolExecutor(ToolExecutorPlugin):
    """Wraps another ToolExecutor and retries on attribute-classified errors with exponential backoff.

    What:
        Delegates to an inner executor and, on returned failures whose
        exception carries ``retryable = True``, sleeps for an exponential
        (optionally jittered) delay and tries again up to ``max_attempts``
        times.  When the exception also carries a ``retry_after_ms``
        attribute, that value is used as a *floor* for the computed delay,
        ensuring rate-limit windows are respected.  Annotates the final
        result's metadata with ``retry_attempts`` / ``retry_delays_ms`` /
        ``retry_reasons``.  ``execute_stream`` is a transparent passthrough
        (no retry).

    Usage:
        ``{"tool_executor": {"type": "retry", "config": {"inner":
        {"type": "safe"}, "max_attempts": 3, "initial_delay_ms": 200,
        "backoff_multiplier": 2.0, "max_delay_ms": 5000,
        "jitter": "equal"}}}``

    Jitter modes (AWS-standard):
        - ``"none"``  — deterministic exponential backoff
        - ``"full"``  — uniform in [0, delay]
        - ``"equal"`` — half fixed + half random: delay//2 + randint(0, delay//2)

    Classification:
        An exception is retryable iff ``getattr(exc, "retryable", False) is True``.
        This subsumes all built-in retryable error classes and any user subclass
        that sets ``retryable = True``.

    Depends on:
        - the wrapped inner ``ToolExecutorPlugin`` loaded via
          :func:`openagents.plugins.loader.load_plugin`
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_attempts: int = 3
        initial_delay_ms: int = 200
        backoff_multiplier: float = 2.0
        max_delay_ms: int = 5_000
        jitter: Literal["none", "full", "equal"] = "equal"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        cfg = self.Config.model_validate(self.config)
        self._max_attempts = max(1, cfg.max_attempts)
        self._initial_delay_ms = max(0, cfg.initial_delay_ms)
        self._backoff = max(1.0, cfg.backoff_multiplier)
        self._max_delay_ms = max(self._initial_delay_ms, cfg.max_delay_ms)
        self._jitter = cfg.jitter
        self._inner = self._load_inner(cfg.inner)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import ToolExecutorRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("tool_executor", ToolExecutorRef(**ref), required_methods=("execute", "execute_stream"))

    def _should_retry(self, exc: Exception | None) -> bool:
        return getattr(exc, "retryable", False) is True

    def _delay_for(self, attempt: int, exc: Exception | None = None) -> int:
        base_ms = int(min(self._initial_delay_ms * (self._backoff**attempt), self._max_delay_ms))
        floor_ms = int(getattr(exc, "retry_after_ms", 0) or 0)
        delay_ms = max(base_ms, floor_ms)
        if self._jitter == "full":
            return random.randint(0, delay_ms)
        if self._jitter == "equal":
            half = delay_ms // 2
            return half + random.randint(0, half)
        return delay_ms  # "none"

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
                    metadata["retry_reasons"] = reasons
                return result.model_copy(update={"metadata": metadata})
            last_result = result
            if attempt + 1 >= self._max_attempts:
                break
            delay_ms = self._delay_for(attempt, result.exception)
            delays.append(delay_ms)
            reasons.append(type(result.exception).__name__ if result.exception else "unknown")
            await asyncio.sleep(delay_ms / 1000)
        if last_result is None:
            raise RuntimeError("RetryToolExecutor.execute: no inner result produced; this is a programming error")
        metadata = dict(last_result.metadata or {})
        metadata["retry_attempts"] = self._max_attempts
        metadata["retry_delays_ms"] = delays
        metadata["retry_reasons"] = reasons
        return last_result.model_copy(update={"metadata": metadata})

    async def execute_stream(self, request: ToolExecutionRequest):
        async for chunk in self._inner.execute_stream(request):
            yield chunk
