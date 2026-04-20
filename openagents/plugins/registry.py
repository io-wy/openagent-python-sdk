"""Builtin plugin registry."""

from __future__ import annotations

from typing import Any

from openagents.decorators import (
    _CONTEXT_ASSEMBLER_REGISTRY,
    _EVENT_REGISTRY,
    _MEMORY_REGISTRY,
    _PATTERN_REGISTRY,
    _RUNTIME_REGISTRY,
    _SESSION_REGISTRY,
    _TOOL_EXECUTOR_REGISTRY,
    _TOOL_REGISTRY,
)
from openagents.plugins.builtin.context.head_tail import HeadTailContextAssembler
from openagents.plugins.builtin.context.importance_weighted import ImportanceWeightedContextAssembler
from openagents.plugins.builtin.context.sliding_window import SlidingWindowContextAssembler
from openagents.plugins.builtin.context.truncating import TruncatingContextAssembler
from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus
from openagents.plugins.builtin.events.otel_bridge import OtelEventBusBridge
from openagents.plugins.builtin.events.rich_console import RichConsoleEventBus
from openagents.plugins.builtin.memory.buffer import BufferMemory
from openagents.plugins.builtin.memory.chain import ChainMemory
from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory
from openagents.plugins.builtin.memory.mem0_memory import Mem0Memory
from openagents.plugins.builtin.memory.window_buffer import WindowBufferMemory
from openagents.plugins.builtin.pattern.plan_execute import PlanExecutePattern
from openagents.plugins.builtin.pattern.react import ReActPattern
from openagents.plugins.builtin.pattern.reflexion import ReflexionPattern
from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime
from openagents.plugins.builtin.session.in_memory import InMemorySessionManager
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager
from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager
from openagents.plugins.builtin.skills.local import LocalSkillsManager
from openagents.plugins.builtin.tool.common import BuiltinSearchTool
from openagents.plugins.builtin.tool.datetime_tools import (
    CurrentTimeTool,
    DateDiffTool,
    DateParseTool,
)
from openagents.plugins.builtin.tool.file_ops import (
    DeleteFileTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from openagents.plugins.builtin.tool.http_ops import HttpRequestTool
from openagents.plugins.builtin.tool.math_tools import CalcTool, MinMaxTool, PercentageTool
from openagents.plugins.builtin.tool.mcp_tool import McpTool
from openagents.plugins.builtin.tool.memory_tools import RememberPreferenceTool
from openagents.plugins.builtin.tool.network_tools import (
    HostLookupTool,
    QueryParamTool,
    URLBuildTool,
    URLParseTool,
)
from openagents.plugins.builtin.tool.random_tools import (
    RandomChoiceTool,
    RandomIntTool,
    RandomStringTool,
    UUIDTool,
)
from openagents.plugins.builtin.tool.shell_exec import ShellExecTool
from openagents.plugins.builtin.tool.system_ops import (
    ExecuteCommandTool,
    GetEnvTool,
    SetEnvTool,
)
from openagents.plugins.builtin.tool.tavily_search import TavilySearchTool
from openagents.plugins.builtin.tool.text_ops import (
    GrepFilesTool,
    JsonParseTool,
    RipgrepTool,
    TextTransformTool,
)
from openagents.plugins.builtin.tool_executor.filesystem_aware import FilesystemAwareExecutor
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor

# Mapping from kind to decorator registry
_DECORATOR_REGISTRY_MAP: dict[str, dict[str, type[Any]]] = {
    "memory": _MEMORY_REGISTRY,
    "pattern": _PATTERN_REGISTRY,
    "runtime": _RUNTIME_REGISTRY,
    "session": _SESSION_REGISTRY,
    "events": _EVENT_REGISTRY,
    "skills": {},
    "tool_executor": _TOOL_EXECUTOR_REGISTRY,
    "context_assembler": _CONTEXT_ASSEMBLER_REGISTRY,
    "tool": _TOOL_REGISTRY,
}

_BUILTIN_REGISTRY: dict[str, dict[str, type[Any]]] = {
    "memory": {
        "buffer": BufferMemory,
        "chain": ChainMemory,
        "markdown_memory": MarkdownMemory,
        "mem0": Mem0Memory,
        "window_buffer": WindowBufferMemory,
    },
    "pattern": {
        "react": ReActPattern,
        "plan_execute": PlanExecutePattern,
        "reflexion": ReflexionPattern,
    },
    "runtime": {
        "default": DefaultRuntime,
    },
    "session": {
        "in_memory": InMemorySessionManager,
        "jsonl_file": JsonlFileSessionManager,
        "sqlite": SqliteSessionManager,
    },
    "events": {
        "async": AsyncEventBus,
        "file_logging": FileLoggingEventBus,
        "otel_bridge": OtelEventBusBridge,
        "rich_console": RichConsoleEventBus,
    },
    "skills": {
        "local": LocalSkillsManager,
    },
    "tool_executor": {
        "safe": SafeToolExecutor,
        "retry": RetryToolExecutor,
        "filesystem_aware": FilesystemAwareExecutor,
    },
    "context_assembler": {
        "truncating": TruncatingContextAssembler,
        "head_tail": HeadTailContextAssembler,
        "sliding_window": SlidingWindowContextAssembler,
        "importance_weighted": ImportanceWeightedContextAssembler,
    },
    "tool": {
        "builtin_search": BuiltinSearchTool,
        "mcp": McpTool,
        # File operations
        "read_file": ReadFileTool,
        "write_file": WriteFileTool,
        "list_files": ListFilesTool,
        "delete_file": DeleteFileTool,
        # Text operations
        "grep_files": GrepFilesTool,
        "ripgrep": RipgrepTool,
        "json_parse": JsonParseTool,
        "text_transform": TextTransformTool,
        # HTTP operations
        "http_request": HttpRequestTool,
        # Memory tools
        "remember_preference": RememberPreferenceTool,
        # Shell execution
        "shell_exec": ShellExecTool,
        # Web search
        "tavily_search": TavilySearchTool,
        # System operations
        "execute_command": ExecuteCommandTool,
        "get_env": GetEnvTool,
        "set_env": SetEnvTool,
        # DateTime operations
        "current_time": CurrentTimeTool,
        "date_parse": DateParseTool,
        "date_diff": DateDiffTool,
        # Random operations
        "random_int": RandomIntTool,
        "random_choice": RandomChoiceTool,
        "random_string": RandomStringTool,
        "uuid": UUIDTool,
        # Network operations
        "url_parse": URLParseTool,
        "url_build": URLBuildTool,
        "query_param": QueryParamTool,
        "host_lookup": HostLookupTool,
        # Math operations
        "calc": CalcTool,
        "percentage": PercentageTool,
        "min_max": MinMaxTool,
    },
}


def get_builtin_plugin_class(kind: str, name: str) -> type[Any] | None:
    """Get a plugin class by kind and name.

    Checks both builtin registry and decorator registry.
    """
    # First check builtin registry
    builtin = _BUILTIN_REGISTRY.get(kind, {}).get(name)
    if builtin is not None:
        return builtin

    # Then check decorator registry
    decorator_reg = _DECORATOR_REGISTRY_MAP.get(kind, {})
    return decorator_reg.get(name)


def has_builtin_plugin(kind: str, name: str) -> bool:
    """Check whether a builtin plugin name already exists for the kind."""
    return name in _BUILTIN_REGISTRY.get(kind, {})


def list_builtin_plugins(kind: str) -> list[str]:
    """List all available plugins for a given kind.

    Includes both builtin and decorator-registered plugins.
    """
    builtin_keys = set(_BUILTIN_REGISTRY.get(kind, {}).keys())
    decorator_keys = set(_DECORATOR_REGISTRY_MAP.get(kind, {}).keys())
    return sorted(builtin_keys | decorator_keys)
