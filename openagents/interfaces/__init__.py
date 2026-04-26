"""Contracts for plugin development."""

from .context import ContextAssemblerPlugin, ContextAssemblyResult
from .events import (
    RUNTIME_SHUTDOWN_COMPLETED,
    RUNTIME_SHUTDOWN_REQUESTED,
    RUNTIME_SHUTDOWN_STARTED,
    EventBusPlugin,
    RuntimeEvent,
)
from .followup import FollowupResolution
from .memory import MemoryPlugin
from .pattern import ExecutionContext, PatternPlugin
from .plugin import BasePlugin
from .response_repair import ResponseRepairDecision
from .run_context import RunContext
from .runtime import (
    RunArtifact,
    RunBudget,
    RunRequest,
    RunResult,
    RuntimePlugin,
    RunUsage,
    StopReason,
)
from .session import (
    SessionArtifact,
    SessionCheckpoint,
    SessionManagerPlugin,
)
from .skills import SessionSkillSummary, SkillsPlugin
from .tool import (
    PermanentToolError,
    PolicyDecision,
    RetryableToolError,
    ToolError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
    ToolExecutor,
    ToolExecutorPlugin,
    ToolPlugin,
    ToolResult,
)

__all__ = [
    "BasePlugin",
    "ExecutionContext",
    "RunContext",
    "ContextAssemblerPlugin",
    "ContextAssemblyResult",
    "FollowupResolution",
    "MemoryPlugin",
    "PatternPlugin",
    "ResponseRepairDecision",
    "SkillsPlugin",
    "SessionSkillSummary",
    "PolicyDecision",
    "ToolPlugin",
    "ToolExecutionSpec",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolExecutorPlugin",
    "ToolError",
    "RetryableToolError",
    "PermanentToolError",
    "ToolResult",
    "RunBudget",
    "RunRequest",
    "RunResult",
    "RunUsage",
    "RunArtifact",
    "StopReason",
    "RuntimePlugin",
    "SessionArtifact",
    "SessionCheckpoint",
    "SessionManagerPlugin",
    "EventBusPlugin",
    "RuntimeEvent",
    "RUNTIME_SHUTDOWN_REQUESTED",
    "RUNTIME_SHUTDOWN_STARTED",
    "RUNTIME_SHUTDOWN_COMPLETED",
]
