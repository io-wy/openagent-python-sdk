"""Tool plugin contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openagents.errors.exceptions import (
    OpenAgentsError,
    PermanentToolError,
    RetryableToolError,
    ToolError,
    ToolNotFoundError,
    ToolTimeoutError,
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
    interrupt_behavior: str = "block"
    side_effects: str = "unknown"
    approval_mode: str = "inherit"
    default_timeout_ms: int | None = None
    reads_files: bool = False
    writes_files: bool = False


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
    context: "RunContext[Any] | None" = None
    execution_spec: ToolExecutionSpec = Field(default_factory=ToolExecutionSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    """Structured result for tool execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_id: str
    success: bool
    data: Any = None
    error: str | None = None
    exception: OpenAgentsError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ExecutionPolicy(Protocol):
    """Policy hook for tool execution."""

    async def evaluate(self, request: ToolExecutionRequest) -> PolicyDecision: ...


@runtime_checkable
class ToolExecutor(Protocol):
    """Executor hook between patterns and tool implementations."""

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...

    async def execute_stream(
        self,
        request: ToolExecutionRequest,
    ) -> AsyncIterator[dict[str, Any]]: ...


class ExecutionPolicyPlugin(BasePlugin):
    """Optional base class for execution policies."""

    async def evaluate(self, request: ToolExecutionRequest) -> PolicyDecision:
        return PolicyDecision(allowed=True)


class ToolExecutorPlugin(BasePlugin):
    """Optional base class for tool executors."""

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
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
        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk


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

    async def fallback(
        self,
        error: Exception,
        params: dict[str, Any],
        context: "RunContext[Any] | None",
    ) -> Any:
        """Fallback handler when invoke fails."""
        raise error
