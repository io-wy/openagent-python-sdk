"""Safe builtin tool executor."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from openagents.errors.exceptions import ToolCancelledError, ToolError, ToolTimeoutError
from openagents.interfaces.tool import PolicyDecision, ToolExecutionRequest, ToolExecutionResult, ToolExecutorPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class SafeToolExecutor(TypedConfigPluginMixin, ToolExecutorPlugin):
    """Builtin tool executor with basic validation and timeout handling.

    What:
        Runs ``tool.invoke`` under ``asyncio.wait_for`` with the
        per-request or default timeout. Calls ``tool.validate_params``
        first if present and short-circuits with a ToolError on
        failure. Returns a ToolExecutionResult with timeout
        metadata; never raises directly.

    Usage:
        ``{"tool_executor": {"type": "safe", "config":
        {"default_timeout_ms": 30000, "allow_stream_passthrough":
        true}}}``

    Depends on:
        - the wrapped tool's ``invoke`` / ``invoke_stream``
        - per-tool ``execution_spec().default_timeout_ms`` (optional)
    """

    class Config(BaseModel):
        default_timeout_ms: int = 30_000
        allow_stream_passthrough: bool = True
        command_allowlist: list[str] | None = None

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()
        self._default_timeout_ms = self.cfg.default_timeout_ms
        self._allow_stream_passthrough = self.cfg.allow_stream_passthrough
        self._command_allowlist = self.cfg.command_allowlist

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        """Enforce executor-level command_allowlist for shell_exec tools.

        When ``command_allowlist`` is configured, only ``shell_exec``
        invocations whose argv[0] is in the list are allowed.  Other
        tools pass through unaffected.  This lets security policy live
        in the executor config rather than per-tool.
        """
        if self._command_allowlist is None:
            return PolicyDecision(allowed=True)

        if request.tool_id != "shell_exec":
            return PolicyDecision(allowed=True)

        command = (request.params or {}).get("command", "")
        if isinstance(command, list):
            argv = [str(c) for c in command]
        else:
            import shlex

            argv = shlex.split(str(command))

        if not argv:
            return PolicyDecision(allowed=False, reason="shell_exec command is empty")

        first = argv[0]
        import os

        if os.sep in first or (os.altsep and os.altsep in first):
            return PolicyDecision(
                allowed=False,
                reason=f"command {first!r} must be a bare name (no path) when command_allowlist is active",
            )

        if first not in self._command_allowlist:
            return PolicyDecision(
                allowed=False,
                reason=f"command {first!r} not in allowlist {self._command_allowlist!r}",
            )

        return PolicyDecision(allowed=True)

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            msg = f"policy denied: {decision.reason}"
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=msg,
                exception=ToolError(msg, tool_name=request.tool_id),
            )

        validator = getattr(request.tool, "validate_params", None)
        if callable(validator):
            is_valid, error = validator(request.params or {})
            if not is_valid:
                exc = ToolError(
                    error or f"Invalid params for tool '{request.tool_id}'",
                    tool_name=request.tool_id,
                    hint=f"Inspect tool '{request.tool_id}' schema via tool.schema() to see required fields",
                )
                return ToolExecutionResult(
                    tool_id=request.tool_id,
                    success=False,
                    error=str(exc),
                    exception=exc,
                )

        timeout_ms = request.execution_spec.default_timeout_ms or self._default_timeout_ms
        timeout_s = timeout_ms / 1000 if timeout_ms else None
        cancel_event = request.cancel_event
        interrupt_behavior = str(request.execution_spec.interrupt_behavior or "cancel").lower()

        invoke_task = asyncio.create_task(request.tool.invoke(request.params or {}, request.context))
        try:
            if cancel_event is None and timeout_s is None:
                data = await invoke_task
            else:
                waiters: list[asyncio.Task] = [invoke_task]
                cancel_task: asyncio.Task | None = None
                timeout_task: asyncio.Task | None = None
                if cancel_event is not None:
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    waiters.append(cancel_task)
                if timeout_s is not None:
                    timeout_task = asyncio.create_task(asyncio.sleep(timeout_s))
                    waiters.append(timeout_task)
                done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

                if invoke_task in done:
                    for t in pending:
                        t.cancel()
                    # Raises if invoke_task failed — caught below and wrapped in ToolError.
                    data = invoke_task.result()
                elif cancel_task is not None and cancel_task in done:
                    if interrupt_behavior == "block":
                        # "block" mode trusts the tool to finish; both cancel AND timeout are ignored.
                        if timeout_task is not None:
                            timeout_task.cancel()
                        data = await invoke_task
                    else:
                        invoke_task.cancel()
                        if timeout_task is not None:
                            timeout_task.cancel()
                        try:
                            await invoke_task
                        except asyncio.CancelledError:
                            pass  # expected: we just cancelled it
                        except Exception:
                            pass  # tool raised concurrently with cancel; cancel wins
                        cancelled_exc = ToolCancelledError(
                            f"Tool '{request.tool_id}' cancelled before completion",
                            tool_name=request.tool_id,
                        )
                        return ToolExecutionResult(
                            tool_id=request.tool_id,
                            success=False,
                            error=str(cancelled_exc),
                            exception=cancelled_exc,
                            metadata={"timeout_ms": timeout_ms, "cancelled": True},
                        )
                else:
                    # timeout won
                    invoke_task.cancel()
                    if cancel_task is not None:
                        cancel_task.cancel()
                    try:
                        await invoke_task
                    except asyncio.CancelledError:
                        pass  # expected: we just cancelled it
                    except Exception:
                        pass  # tool raised concurrently with timeout; timeout wins
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

            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=True,
                data=data,
                metadata={"timeout_ms": timeout_ms},
            )
        except asyncio.CancelledError:
            # Caller cancelled us from outside — propagate, don't mask.
            raise
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
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            msg = f"policy denied: {decision.reason}"
            raise ToolError(msg, tool_name=request.tool_id)

        if not self._allow_stream_passthrough:
            result = await self.execute(request)
            yield {"type": "result", "data": result.data, "error": result.error}
            return

        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk
