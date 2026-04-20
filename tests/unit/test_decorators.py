"""Tests for decorators module."""

import warnings

from openagents.decorators import (
    context_assembler,
    get_context_assembler,
    get_event_bus,
    get_memory,
    get_pattern,
    get_runtime,
    get_session,
    get_tool,
    get_tool_executor,
    list_context_assemblers,
    list_event_buses,
    list_memories,
    list_patterns,
    list_runtimes,
    list_sessions,
    list_tool_executors,
    list_tools,
    memory,
    pattern,
    runtime,
    session,
    tool,
    tool_executor,
)


def test_tool_decorator_without_args():
    """Test @tool decorator without arguments."""
    # Import inside function to ensure correct module context
    from openagents.decorators import get_tool as get_tool_func
    from openagents.decorators import tool as tool_decorator

    @tool_decorator
    async def my_tool(params, context):
        return {"result": "ok"}

    assert get_tool_func("my_tool") is my_tool
    assert my_tool._tool_name == "my_tool"
    assert my_tool._is_tool is True


def test_tool_decorator_with_args():
    """Test @tool decorator with name and description."""

    @tool(name="search", description="Search the web")
    async def search_tool(params, context):
        return {"result": []}

    assert get_tool("search") is search_tool
    assert search_tool._tool_name == "search"
    assert search_tool._tool_description == "Search the web"


def test_tool_decorator_class():
    """Test @tool decorator with a class."""

    @tool(name="my_class", description="A tool class")
    class MyToolClass:
        pass

    assert get_tool("my_class") is MyToolClass
    assert MyToolClass._tool_name == "my_class"


def test_tool_decorator_warns_when_shadowing_builtin_name():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        @tool(name="calc")
        async def builtin_shadow(params, context):
            return {"ok": True}

    assert get_tool("calc") is builtin_shadow
    assert any("builtin" in str(item.message).lower() for item in caught)


def test_pattern_decorator_without_args():
    """Test @pattern decorator without arguments."""

    @pattern
    class MyPattern:
        async def execute(self):
            pass

        async def react(self):
            pass

    assert get_pattern("MyPattern") is MyPattern
    assert MyPattern._pattern_name == "MyPattern"
    assert MyPattern._is_pattern is True


def test_pattern_decorator_with_name():
    """Test @pattern decorator with custom name."""

    @pattern(name="custom_pattern")
    class CustomPattern:
        pass

    assert get_pattern("custom_pattern") is CustomPattern
    assert CustomPattern._pattern_name == "custom_pattern"


def test_pattern_decorator_syntax():
    """Test @pattern() syntax (with parentheses)."""

    @pattern(name="another_pattern")
    class AnotherPattern:
        pass

    assert get_pattern("another_pattern") is AnotherPattern


def test_memory_decorator_without_args():
    """Test @memory decorator without arguments."""

    @memory
    class MyMemory:
        async def inject(self, context):
            pass

        async def writeback(self, context):
            pass

    assert get_memory("MyMemory") is MyMemory
    assert MyMemory._memory_name == "MyMemory"
    assert MyMemory._is_memory is True


def test_memory_decorator_with_name():
    """Test @memory decorator with custom name."""

    @memory(name="custom_memory")
    class CustomMemory:
        pass

    assert get_memory("custom_memory") is CustomMemory


def test_runtime_decorator_without_args():
    """Test @runtime decorator without arguments."""

    @runtime
    class MyRuntime:
        pass

    assert get_runtime("MyRuntime") is MyRuntime
    assert MyRuntime._runtime_name == "MyRuntime"
    assert MyRuntime._is_runtime is True


def test_runtime_decorator_with_name():
    """Test @runtime decorator with custom name."""

    @runtime(name="custom_runtime")
    class CustomRuntime:
        pass

    assert get_runtime("custom_runtime") is CustomRuntime


def test_session_decorator_without_args():
    """Test @session decorator without arguments."""

    @session
    class MySession:
        pass

    assert get_session("MySession") is MySession
    assert MySession._session_name == "MySession"
    assert MySession._is_session is True


def test_session_decorator_with_name():
    """Test @session decorator with custom name."""

    @session(name="custom_session")
    class CustomSession:
        pass

    assert get_session("custom_session") is CustomSession


def test_event_bus_decorator_without_args():
    """Test @event_bus decorator without arguments."""
    from openagents.decorators import event_bus as event_bus_decorator

    @event_bus_decorator
    class MyEventBus:
        pass

    assert get_event_bus("MyEventBus") is MyEventBus
    # Note: event_bus decorator uses _event_name, not _event_bus_name
    assert MyEventBus._event_name == "MyEventBus"
    assert MyEventBus._is_event_bus is True


def test_event_bus_decorator_with_name():
    """Test @event_bus decorator with custom name."""
    from openagents.decorators import event_bus as event_bus_decorator

    @event_bus_decorator(name="custom_event_bus")
    class CustomEventBus:
        pass

    assert get_event_bus("custom_event_bus") is CustomEventBus


def test_tool_executor_decorator_with_name():
    @tool_executor(name="custom_tool_executor")
    class CustomToolExecutor:
        pass

    assert get_tool_executor("custom_tool_executor") is CustomToolExecutor


def test_context_assembler_decorator_with_name():
    @context_assembler(name="custom_context_assembler")
    class CustomContextAssembler:
        pass

    assert get_context_assembler("custom_context_assembler") is CustomContextAssembler


def test_list_functions():
    """Test list_* functions return lists."""
    tools = list_tools()
    assert isinstance(tools, list)

    patterns = list_patterns()
    assert isinstance(patterns, list)

    memories = list_memories()
    assert isinstance(memories, list)

    runtimes = list_runtimes()
    assert isinstance(runtimes, list)

    sessions = list_sessions()
    assert isinstance(sessions, list)

    event_buses = list_event_buses()
    assert isinstance(event_buses, list)

    tool_executors = list_tool_executors()
    assert isinstance(tool_executors, list)

    context_assemblers = list_context_assemblers()
    assert isinstance(context_assemblers, list)


def test_get_nonexistent():
    """Test get_* functions return None for unknown names."""
    assert get_tool("nonexistent_tool") is None
    assert get_pattern("nonexistent_pattern") is None
    assert get_memory("nonexistent_memory") is None
    assert get_runtime("nonexistent_runtime") is None
    assert get_session("nonexistent_session") is None
    assert get_event_bus("nonexistent_event_bus") is None
    assert get_tool_executor("nonexistent_tool_executor") is None
    assert get_context_assembler("nonexistent_context_assembler") is None


def test_decorator_preserves_functionality():
    """Test that decorators preserve original function/class functionality."""

    @tool(name="preserved")
    async def my_func(x, y):
        return x + y

    # Original function should still work
    import asyncio

    result = asyncio.run(my_func(1, 2))
    assert result == 3
