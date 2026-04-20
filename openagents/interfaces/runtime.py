"""Runtime plugin contract - core execution orchestration."""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from openagents.errors.exceptions import OpenAgentsError

from .plugin import BasePlugin


class StopReason(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    MAX_STEPS = "max_steps"
    BUDGET_EXHAUSTED = "budget_exhausted"


RUN_STOP_COMPLETED = StopReason.COMPLETED.value
RUN_STOP_FAILED = StopReason.FAILED.value
RUN_STOP_CANCELLED = StopReason.CANCELLED.value
RUN_STOP_TIMEOUT = StopReason.TIMEOUT.value


class RunBudget(BaseModel):
    """Optional execution budget for a single run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    max_steps: int | None = None
    max_duration_ms: int | None = None
    max_tool_calls: int | None = None
    max_validation_retries: int | None = 3
    max_cost_usd: float | None = None
    max_resume_attempts: int | None = 3


class RunArtifact(BaseModel):
    """Artifact emitted by a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    kind: str = "generic"
    payload: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunUsage(BaseModel):
    """Usage statistics collected during a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_cached: int = 0
    input_tokens_cache_creation: int = 0
    cost_usd: float | None = None
    cost_breakdown: dict[str, float] = Field(default_factory=dict)


class RunRequest(BaseModel):
    """Structured runtime request."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_id: str
    session_id: str
    input_text: str
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    context_hints: dict[str, Any] = Field(default_factory=dict)
    budget: RunBudget | None = None
    deps: Any = None
    output_type: type[BaseModel] | None = None
    durable: bool = False
    resume_from_checkpoint: str | None = None


OutputT = TypeVar("OutputT")


class RunResult(BaseModel, Generic[OutputT]):
    """Structured runtime result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    final_output: OutputT | None = None
    stop_reason: StopReason = StopReason.COMPLETED
    usage: RunUsage = Field(default_factory=RunUsage)
    artifacts: list[RunArtifact] = Field(default_factory=list)
    error: str | None = None
    exception: OpenAgentsError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunStreamChunkKind(str, Enum):
    RUN_STARTED = "run.started"
    LLM_DELTA = "llm.delta"
    LLM_FINISHED = "llm.finished"
    TOOL_STARTED = "tool.started"
    TOOL_DELTA = "tool.delta"
    TOOL_FINISHED = "tool.finished"
    ARTIFACT = "artifact"
    VALIDATION_RETRY = "validation.retry"
    CHECKPOINT_SAVED = "run.checkpoint_saved"
    RESUME_ATTEMPTED = "run.resume_attempted"
    RESUME_SUCCEEDED = "run.resume_succeeded"
    RUN_FINISHED = "run.finished"


class RunStreamChunk(BaseModel):
    """One chunk of a streamed run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: RunStreamChunkKind
    run_id: str
    session_id: str = ""
    agent_id: str = ""
    sequence: int = 0
    timestamp_ms: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    result: "RunResult | None" = None


class RuntimePlugin(BasePlugin):
    """Base runtime plugin.

    Implementations control the execution lifecycle, orchestration flow,
    and how agents are run. Runtime is the top-level coordinator.
    """

    async def initialize(self) -> None:
        """Initialize runtime before first use.

        Called once during Runtime startup. Use for:
        - Loading configurations
        - Establishing connections
        - Setting up resources
        """
        pass

    async def validate(self) -> None:
        """Validate runtime configuration.

        Called after initialize(). Should raise if configuration is invalid.
        """
        pass

    async def health_check(self) -> bool:
        """Check runtime health status.

        Returns:
            True if runtime is healthy, False otherwise
        """
        return True

    async def run(
        self,
        *,
        request: RunRequest,
        **kwargs: Any,
    ) -> RunResult:
        """Execute an agent run with the given request.

        Args:
            request: Structured run request
            **kwargs: Runtime-specific execution dependencies

        Returns:
            Structured execution result
        """
        raise NotImplementedError("RuntimePlugin.run must be implemented")

    async def pause(self) -> None:
        """Pause runtime execution.

        Suspends any ongoing runs. State should be preserved.
        """
        pass

    async def resume(self) -> None:
        """Resume runtime execution.

        Continues previously paused runs.
        """
        pass

    async def close(self) -> None:
        """Cleanup runtime resources.

        Called during Runtime shutdown. Use for:
        - Closing connections
        - Flushing buffers
        - Releasing resources
        """
        pass


# Capability constants for runtime plugins
RUNTIME_RUN = "runtime.run"
RUNTIME_MANAGE = "runtime.manage"  # start/stop/pause runtime
RUNTIME_LIFECYCLE = "runtime.lifecycle"  # initialize/validate/health_check


RunStreamChunk.model_rebuild()
