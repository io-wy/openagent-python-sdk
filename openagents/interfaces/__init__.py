"""Contracts for plugin development."""

from .capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    PATTERN_REACT,
    TOOL_INVOKE,
)
from .events import EventBusPlugin, EVENT_EMIT, EVENT_HISTORY, EVENT_SUBSCRIBE, RuntimeEvent
from .memory import MemoryPlugin
from .pattern import ExecutionContext, PatternPlugin
from .plugin import BasePlugin
from .runtime import RUNTIME_LIFECYCLE, RUNTIME_MANAGE, RUNTIME_RUN, RuntimePlugin
from .session import SESSION_MANAGE, SESSION_STATE, SessionManagerPlugin
from .tool import (
    PermanentToolError,
    RetryableToolError,
    ToolError,
    ToolPlugin,
    ToolResult,
)

__all__ = [
    "BasePlugin",
    "ExecutionContext",
    "MemoryPlugin",
    "PatternPlugin",
    "ToolPlugin",
    "ToolError",
    "RetryableToolError",
    "PermanentToolError",
    "ToolResult",
    "RuntimePlugin",
    "SessionManagerPlugin",
    "EventBusPlugin",
    "RuntimeEvent",
    # Capabilities
    "MEMORY_INJECT",
    "MEMORY_WRITEBACK",
    "PATTERN_REACT",
    "TOOL_INVOKE",
    "RUNTIME_RUN",
    "RUNTIME_MANAGE",
    "RUNTIME_LIFECYCLE",
    "SESSION_MANAGE",
    "SESSION_STATE",
    "EVENT_SUBSCRIBE",
    "EVENT_EMIT",
    "EVENT_HISTORY",
    "RUNTIME_SHUTDOWN",
    "RUNTIME_SHUTDOWN_STARTED",
    "RUNTIME_SHUTDOWN_COMPLETED",
]
