"""Plugin decorators for easy registration.

Usage:
    @tool
    async def my_tool(params, context):
        '''My tool description'''
        return {"result": "ok"}

    @tool(name="custom_name")
    async def another_tool(params, context):
        ...

    @memory
    class MyMemory:
        async def inject(self, context): ...
        async def writeback(self, context): ...
        async def retrieve(self, query, context): ...

    @pattern
    class MyPattern:
        async def execute(self): ...

    @runtime
    class MyRuntime:
        async def run(self, ...): ...

    @session
    class MySession:
        ...
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, TypeVar

# Global registries for decorated plugins
_TOOL_REGISTRY: dict[str, type] = {}
_PATTERN_REGISTRY: dict[str, type] = {}
_MEMORY_REGISTRY: dict[str, type] = {}
_RUNTIME_REGISTRY: dict[str, type] = {}
_SESSION_REGISTRY: dict[str, type] = {}
_EVENT_REGISTRY: dict[str, type] = {}
_TOOL_EXECUTOR_REGISTRY: dict[str, type] = {}
_CONTEXT_ASSEMBLER_REGISTRY: dict[str, type] = {}


# Type helpers
F = TypeVar("F", bound=Callable[..., Any])


def _register_plugin(
    *,
    registry: dict[str, type],
    kind: str,
    name: str,
    plugin: Any,
) -> None:
    if name in registry:
        warnings.warn(
            f"{kind.title()} '{name}' is being overridden: {registry[name].__name__} -> {plugin.__name__}",
            stacklevel=3,
        )

    try:
        from openagents.plugins.registry import has_builtin_plugin
    except Exception:  # pragma: no cover - defensive import guard
        has_builtin_plugin = None

    if callable(has_builtin_plugin) and has_builtin_plugin(kind, name):
        warnings.warn(
            f"{kind.title()} '{name}' shadows a builtin plugin of the same name.",
            stacklevel=3,
        )

    registry[name] = plugin


def tool(name: str | None = None, description: str = ""):
    """Decorator to register a tool function or class.

    Usage:
        @tool
        async def my_tool(params, context):
            return {"result": "ok"}

        @tool(name="search", description="Search the web")
        async def search_tool(params, context):
            ...
    """
    # Support both @tool and @tool() syntax
    # If name is a callable (function/class), we're being used as @tool (not @tool())
    if callable(name):
        fn_or_cls = name
        tool_name = getattr(fn_or_cls, "__name__", fn_or_cls.__class__.__name__ if not callable(fn_or_cls) else "tool")
        _register_plugin(registry=_TOOL_REGISTRY, kind="tool", name=tool_name, plugin=fn_or_cls)
        if callable(fn_or_cls):
            fn_or_cls._tool_name = tool_name
            fn_or_cls._tool_description = ""
            fn_or_cls._is_tool = True
        return fn_or_cls

    def decorator(fn_or_cls: F) -> F:
        tool_name = name or getattr(
            fn_or_cls, "__name__", fn_or_cls.__class__.__name__ if not callable(fn_or_cls) else "tool"
        )
        _register_plugin(registry=_TOOL_REGISTRY, kind="tool", name=tool_name, plugin=fn_or_cls)

        # Attach metadata
        if callable(fn_or_cls):
            fn_or_cls._tool_name = tool_name
            fn_or_cls._tool_description = description
            fn_or_cls._is_tool = True

        return fn_or_cls

    return decorator


def pattern(name: str | None = None):
    """Decorator to register a pattern class.

    Usage:
        @pattern
        class MyPattern:
            async def execute(self):
                ...

            async def react(self):
                ...

        # Or with custom name:
        @pattern(name="custom")
        class CustomPattern:
            ...
    """
    # Support both @pattern and @pattern() syntax
    if isinstance(name, type):
        cls = name
        pattern_name = cls.__name__
        _register_plugin(registry=_PATTERN_REGISTRY, kind="pattern", name=pattern_name, plugin=cls)
        cls._pattern_name = pattern_name
        cls._is_pattern = True
        return cls

    def decorator(cls: type) -> type:
        pattern_name = name or cls.__name__
        _register_plugin(registry=_PATTERN_REGISTRY, kind="pattern", name=pattern_name, plugin=cls)

        cls._pattern_name = pattern_name
        cls._is_pattern = True
        return cls

    return decorator


def memory(name: str | None = None):
    """Decorator to register a memory class.

    Usage:
        @memory
        class MyMemory:
            async def inject(self, context):
                '''Inject memory into context'''
                context.memory_view["history"] = [...]

            async def writeback(self, context):
                '''Save current interaction'''
                ...

            async def retrieve(self, query, context):
                '''Search memory for relevant info'''
                return [...]

        # Or with custom name:
        @memory(name="custom")
        class CustomMemory:
            ...
    """
    # Support both @memory and @memory() syntax
    # If name is actually a class, we're being used as @memory (not @memory())
    if isinstance(name, type):
        cls = name
        memory_name = cls.__name__
        _register_plugin(registry=_MEMORY_REGISTRY, kind="memory", name=memory_name, plugin=cls)
        cls._memory_name = memory_name
        cls._is_memory = True
        return cls

    def decorator(cls: type) -> type:
        memory_name = name or cls.__name__
        _register_plugin(registry=_MEMORY_REGISTRY, kind="memory", name=memory_name, plugin=cls)

        cls._memory_name = memory_name
        cls._is_memory = True
        return cls

    return decorator


def runtime(name: str | None = None):
    """Decorator to register a runtime class.

    Usage:
        @runtime
        class MyRuntime:
            async def run(self, *, request, app_config, agents_by_id, agent_plugins):
                ...  # must return RunResult

        # Or with custom name:
        @runtime(name="custom")
        class CustomRuntime:
            ...
    """
    # Support both @runtime and @runtime() syntax
    if isinstance(name, type):
        cls = name
        runtime_name = cls.__name__
        _register_plugin(registry=_RUNTIME_REGISTRY, kind="runtime", name=runtime_name, plugin=cls)
        cls._runtime_name = runtime_name
        cls._is_runtime = True
        return cls

    def decorator(cls: type) -> type:
        runtime_name = name or cls.__name__
        _register_plugin(registry=_RUNTIME_REGISTRY, kind="runtime", name=runtime_name, plugin=cls)

        cls._runtime_name = runtime_name
        cls._is_runtime = True
        return cls

    return decorator


def session(name: str | None = None):
    """Decorator to register a session manager class.

    Usage:
        @session
        class MySession:
            async def get_state(self, session_id):
                ...

            async def set_state(self, session_id, state):
                ...

        # Or with custom name:
        @session(name="custom")
        class CustomSession:
            ...
    """
    # Support both @session and @session() syntax
    if isinstance(name, type):
        cls = name
        session_name = cls.__name__
        _register_plugin(registry=_SESSION_REGISTRY, kind="session", name=session_name, plugin=cls)
        cls._session_name = session_name
        cls._is_session = True
        return cls

    def decorator(cls: type) -> type:
        session_name = name or cls.__name__
        _register_plugin(registry=_SESSION_REGISTRY, kind="session", name=session_name, plugin=cls)

        cls._session_name = session_name
        cls._is_session = True
        return cls

    return decorator


def event_bus(name: str | None = None):
    """Decorator to register an event bus class.

    Usage:
        @event_bus
        class MyEventBus:
            async def emit(self, event_name, **payload):
                ...

            def subscribe(self, event_name, handler):
                ...

        # Or with custom name:
        @event_bus(name="custom")
        class CustomEventBus:
            ...
    """
    # Support both @event_bus and @event_bus() syntax
    if isinstance(name, type):
        cls = name
        event_name = cls.__name__
        _register_plugin(registry=_EVENT_REGISTRY, kind="events", name=event_name, plugin=cls)
        cls._event_name = event_name
        cls._is_event_bus = True
        return cls

    def decorator(cls: type) -> type:
        event_name = name or cls.__name__
        _register_plugin(registry=_EVENT_REGISTRY, kind="events", name=event_name, plugin=cls)

        cls._event_name = event_name
        cls._is_event_bus = True
        return cls

    return decorator


def tool_executor(name: str | None = None):
    """Decorator to register a tool executor class."""
    if isinstance(name, type):
        cls = name
        executor_name = cls.__name__
        _register_plugin(registry=_TOOL_EXECUTOR_REGISTRY, kind="tool_executor", name=executor_name, plugin=cls)
        cls._tool_executor_name = executor_name
        cls._is_tool_executor = True
        return cls

    def decorator(cls: type) -> type:
        executor_name = name or cls.__name__
        _register_plugin(registry=_TOOL_EXECUTOR_REGISTRY, kind="tool_executor", name=executor_name, plugin=cls)
        cls._tool_executor_name = executor_name
        cls._is_tool_executor = True
        return cls

    return decorator


def context_assembler(name: str | None = None):
    """Decorator to register a context assembler class."""
    if isinstance(name, type):
        cls = name
        assembler_name = cls.__name__
        _register_plugin(
            registry=_CONTEXT_ASSEMBLER_REGISTRY, kind="context_assembler", name=assembler_name, plugin=cls
        )
        cls._context_assembler_name = assembler_name
        cls._is_context_assembler = True
        return cls

    def decorator(cls: type) -> type:
        assembler_name = name or cls.__name__
        _register_plugin(
            registry=_CONTEXT_ASSEMBLER_REGISTRY, kind="context_assembler", name=assembler_name, plugin=cls
        )
        cls._context_assembler_name = assembler_name
        cls._is_context_assembler = True
        return cls

    return decorator


def get_tool(name: str) -> type | None:
    """Get a registered tool by name."""
    return _TOOL_REGISTRY.get(name)


def get_pattern(name: str) -> type | None:
    """Get a registered pattern by name."""
    return _PATTERN_REGISTRY.get(name)


def get_memory(name: str) -> type | None:
    """Get a registered memory by name."""
    return _MEMORY_REGISTRY.get(name)


def get_runtime(name: str) -> type | None:
    """Get a registered runtime by name."""
    return _RUNTIME_REGISTRY.get(name)


def get_session(name: str) -> type | None:
    """Get a registered session by name."""
    return _SESSION_REGISTRY.get(name)


def get_event_bus(name: str) -> type | None:
    """Get a registered event bus by name."""
    return _EVENT_REGISTRY.get(name)


def get_tool_executor(name: str) -> type | None:
    """Get a registered tool executor by name."""
    return _TOOL_EXECUTOR_REGISTRY.get(name)


def get_context_assembler(name: str) -> type | None:
    """Get a registered context assembler by name."""
    return _CONTEXT_ASSEMBLER_REGISTRY.get(name)


def list_tools() -> list[str]:
    """List all registered tool names."""
    return list(_TOOL_REGISTRY.keys())


def list_patterns() -> list[str]:
    """List all registered pattern names."""
    return list(_PATTERN_REGISTRY.keys())


def list_memories() -> list[str]:
    """List all registered memory names."""
    return list(_MEMORY_REGISTRY.keys())


def list_runtimes() -> list[str]:
    """List all registered runtime names."""
    return list(_RUNTIME_REGISTRY.keys())


def list_sessions() -> list[str]:
    """List all registered session names."""
    return list(_SESSION_REGISTRY.keys())


def list_event_buses() -> list[str]:
    """List all registered event bus names."""
    return list(_EVENT_REGISTRY.keys())


def list_tool_executors() -> list[str]:
    """List all registered tool executor names."""
    return list(_TOOL_EXECUTOR_REGISTRY.keys())


def list_context_assemblers() -> list[str]:
    """List all registered context assembler names."""
    return list(_CONTEXT_ASSEMBLER_REGISTRY.keys())
