"""Tool plugin contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from openagents.errors.exceptions import (
    OpenAgentsError,
    PermanentToolError,  # noqa: F401
    RetryableToolError,  # noqa: F401
    ToolError,
    ToolNotFoundError,  # noqa: F401
    ToolTimeoutError,  # noqa: F401
)

from .plugin import BasePlugin

if TYPE_CHECKING:
    from .run_context import RunContext


class ToolResult(BaseModel):
    """Standardized tool result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    data: Any = None
    error: str | None = None
    tool_name: str = ""


class ToolExecutionSpec(BaseModel):
    """Execution metadata for a tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    concurrency_safe: bool = False
    interrupt_behavior: str = "cancel"
    side_effects: str = "unknown"
    approval_mode: str = "inherit"
    default_timeout_ms: int | None = None
    reads_files: bool = False
    writes_files: bool = False
    supports_streaming: bool = False


class PolicyDecision(BaseModel):
    """Tool execution policy decision."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    allowed: bool
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionRequest(BaseModel):
    """Structured request for tool execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_id: str
    tool: Any
    params: dict[str, Any] = Field(default_factory=dict)
    context: Any = None
    execution_spec: ToolExecutionSpec = Field(default_factory=ToolExecutionSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)
    cancel_event: Any | None = None


class ToolExecutionResult(BaseModel):
    """Structured result for tool execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_id: str
    success: bool
    data: Any = None
    error: str | None = None
    exception: OpenAgentsError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchItem(BaseModel):
    """One entry in a batched tool call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    params: dict[str, Any] = Field(default_factory=dict)
    item_id: str = Field(default_factory=lambda: uuid4().hex)


class BatchResult(BaseModel):
    """One result in a batched tool call. Preserves input item_id and order."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    item_id: str
    success: bool
    data: Any = None
    error: str | None = None
    exception: OpenAgentsError | None = None


class JobHandle(BaseModel):
    """Returned by invoke_background(). Serialized back to the LLM as the tool result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: str
    tool_id: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    created_at: float


class JobStatus(BaseModel):
    """Returned by poll_job()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    progress: float | None = None
    result: Any = None
    error: str | None = None


@runtime_checkable
class ToolExecutor(Protocol):
    """Executor hook between patterns and tool implementations."""

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...

    async def execute_stream(
        self,
        request: ToolExecutionRequest,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def execute_batch(
        self,
        requests: list[ToolExecutionRequest],
    ) -> list[ToolExecutionResult]: ...


class ToolExecutorPlugin(BasePlugin):
    """Optional base class for tool executors."""

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        """Override to restrict tool execution. Default: allow all."""
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
        try:
            data = await request.tool.invoke(request.params or {}, request.context)
            return ToolExecutionResult(tool_id=request.tool_id, success=True, data=data)
        except OpenAgentsError as exc:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(exc),
                exception=exc,
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(exc),
                exception=ToolError(str(exc), tool_name=request.tool_id),
            )

    async def execute_stream(
        self,
        request: ToolExecutionRequest,
    ) -> AsyncIterator[dict[str, Any]]:
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            msg = f"policy denied: {decision.reason}"
            raise ToolError(msg, tool_name=request.tool_id)
        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk

    async def execute_batch(
        self,
        requests: list[ToolExecutionRequest],
    ) -> list[ToolExecutionResult]:
        """Default: sequential. Builtins (ConcurrentBatchExecutor) override for parallelism."""
        results: list[ToolExecutionResult] = []
        for req in requests:
            results.append(await self.execute(req))
        return results


class ToolPlugin(BasePlugin):
    """Base tool plugin."""

    # Subclasses can override these
    name: str = ""
    description: str = ""

    @property
    def tool_name(self) -> str:
        """Tool name, defaults to class name."""
        return self.name or self.__class__.__name__

    async def invoke(self, params: dict[str, Any], context: "RunContext[Any] | None") -> Any:
        """Execute tool call synchronously.

        Args:
            params: Tool input parameters
            context: Execution context

        Returns:
            Tool result
        """
        raise NotImplementedError("ToolPlugin.invoke must be implemented")

    def execution_spec(self) -> ToolExecutionSpec:
        """Return execution metadata for this tool."""
        return ToolExecutionSpec()

    async def invoke_stream(
        self, params: dict[str, Any], context: "RunContext[Any] | None"
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute tool call with streaming output.

        Yields partial results as they become available.
        """
        result = await self.invoke(params, context)
        yield {"type": "result", "data": result}

    def schema(self) -> dict[str, Any]:
        """Return JSON Schema for tool parameters."""
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def describe(self) -> dict[str, Any]:
        """Return tool description for LLM consumption."""
        return {
            "name": self.name or self.__class__.__name__,
            "description": self.description or "",
            "parameters": self.schema(),
        }

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate tool parameters."""
        return True, None

    def get_dependencies(self) -> list[str]:
        """Get list of tool IDs this tool depends on."""
        return []

    async def preflight(self, context: "RunContext[Any] | None") -> None:
        """Optional one-shot validation before the first tool call of a run.

        Overridden by tools with external dependencies (e.g. MCP servers,
        subprocess-backed tools) to check install / reachability up front
        and surface a ``PermanentToolError`` with a helpful hint before the
        agent loop runs, instead of mid-step. Default is a no-op so
        existing tools keep working unchanged.
        """
        return None

    async def fallback(
        self,
        error: Exception,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
    ) -> Any:
        """Fallback handler when invoke fails."""
        raise error

    async def invoke_batch(
        self,
        items: list[BatchItem],
        context: "RunContext[Any] | None",
    ) -> list[BatchResult]:
        """Batched invocation. Default: sequential loop over ``invoke``.

        Override when the tool can handle N items cheaper than N invokes
        (MCP bulk calls, multi-file reads, pipelined HTTP).
        Result list length and item_ids must match the input.
        """
        results: list[BatchResult] = []
        for item in items:
            try:
                data = await self.invoke(item.params, context)
                results.append(BatchResult(item_id=item.item_id, success=True, data=data))
            except OpenAgentsError as exc:
                results.append(
                    BatchResult(
                        item_id=item.item_id,
                        success=False,
                        error=str(exc),
                        exception=exc,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                wrapped = ToolError(str(exc), tool_name=self.tool_name)
                results.append(
                    BatchResult(
                        item_id=item.item_id,
                        success=False,
                        error=str(wrapped),
                        exception=wrapped,
                    )
                )
        return results

    async def invoke_background(
        self,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
    ) -> JobHandle:
        """Submit a long-running job; return handle immediately. Default: NotImplementedError."""
        raise NotImplementedError(
            f"{self.tool_name} does not support background execution"
        )

    async def poll_job(
        self,
        handle: JobHandle,
        context: "RunContext[Any] | None",
    ) -> JobStatus:
        """Query background job status. Default: NotImplementedError."""
        raise NotImplementedError(
            f"{self.tool_name} does not support background execution"
        )

    async def cancel_job(
        self,
        handle: JobHandle,
        context: "RunContext[Any] | None",
    ) -> bool:
        """Cancel a background job. Return True if cancelled. Default: NotImplementedError."""
        raise NotImplementedError(
            f"{self.tool_name} does not support background execution"
        )

    def requires_approval(
        self,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
    ) -> bool:
        """Whether this call needs human approval before execution.

        Default reads ``execution_spec().approval_mode``:
          - "always"  -> True
          - "never"   -> False
          - "inherit" -> False (app layer decides elsewhere)
        Override to decide per-parameters.
        """
        return self.execution_spec().approval_mode == "always"

    async def before_invoke(
        self,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
    ) -> None:
        """Per-call pre-hook. Default no-op.

        Distinct from ``preflight`` (run once per run). Use for token refresh,
        per-call metrics, rate-limit token acquisition.
        """
        return None

    async def after_invoke(
        self,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
        result: Any,
        exception: BaseException | None = None,
    ) -> None:
        """Per-call post-hook. Always runs (success or failure). Default no-op.

        ``result`` is None on failure; ``exception`` is set on failure.
        """
        return None


if not TYPE_CHECKING:
    ToolExecutionRequest.model_rebuild()
