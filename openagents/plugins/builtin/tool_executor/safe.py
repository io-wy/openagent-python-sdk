"""Safe builtin tool executor."""

from __future__ import annotations

import asyncio
from typing import Any

from openagents.errors.exceptions import ToolError, ToolTimeoutError
from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionResult, ToolExecutorPlugin


class SafeToolExecutor(ToolExecutorPlugin):
    """Builtin tool executor with basic validation and timeout handling."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        self._default_timeout_ms = int(self.config.get("default_timeout_ms", 30_000))
        self._allow_stream_passthrough = bool(self.config.get("allow_stream_passthrough", True))

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        validator = getattr(request.tool, "validate_params", None)
        if callable(validator):
            is_valid, error = validator(request.params or {})
            if not is_valid:
                exc = ToolError(error or f"Invalid params for tool '{request.tool_id}'", tool_name=request.tool_id)
                return ToolExecutionResult(
                    tool_id=request.tool_id,
                    success=False,
                    error=str(exc),
                    exception=exc,
                )

        timeout_ms = request.execution_spec.default_timeout_ms or self._default_timeout_ms
        timeout_s = timeout_ms / 1000 if timeout_ms else None
        try:
            coro = request.tool.invoke(request.params or {}, request.context)
            data = await asyncio.wait_for(coro, timeout=timeout_s) if timeout_s else await coro
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=True,
                data=data,
                metadata={"timeout_ms": timeout_ms},
            )
        except asyncio.TimeoutError as exc:
            timeout_exc = ToolTimeoutError(
                f"Tool '{request.tool_id}' timed out after {timeout_ms}ms",
                tool_name=request.tool_id,
            )
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(timeout_exc),
                exception=timeout_exc,
                metadata={"timeout_ms": timeout_ms},
            )
        except Exception as exc:
            wrapped_exc = exc if isinstance(exc, ToolError) else ToolError(str(exc), tool_name=request.tool_id)
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(wrapped_exc),
                exception=wrapped_exc,
                metadata={"timeout_ms": timeout_ms},
            )

    async def execute_stream(self, request: ToolExecutionRequest):
        if not self._allow_stream_passthrough:
            result = await self.execute(request)
            yield {"type": "result", "data": result.data, "error": result.error}
            return

        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk
