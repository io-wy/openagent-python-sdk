"""Test plugins registered via decorators."""

from __future__ import annotations

from typing import Any

from openagents import context_assembler, memory, pattern, tool, tool_executor
from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    PATTERN_EXECUTE,
    PATTERN_REACT,
    TOOL_INVOKE,
)
from openagents.interfaces.context import ContextAssemblyResult


@memory
class DecoratorMemory:
    """Memory registered via decorator."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {MEMORY_INJECT}

    async def inject(self, context: Any) -> None:
        context.memory_view["from_decorator"] = True


@pattern
class DecoratorPattern:
    """Pattern registered via decorator."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(
        self,
        agent_id: str,
        session_id: str,
        input_text: str,
        state: dict[str, Any],
        tools: dict[str, Any],
        llm_client: Any,
        llm_options: Any,
        event_bus: Any,
    ) -> None:
        from openagents.interfaces.pattern import ExecutionContext

        self.context = ExecutionContext(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
        )

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": "decorated"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


@tool(name="decorated_tool", description="A tool registered via decorator")
class DecoratorTool:
    """Tool registered via decorator."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {TOOL_INVOKE}

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        return {"from_decorator": True, "params": params}


@tool_executor(name="decorated_tool_executor")
class DecoratorToolExecutor:
    def __init__(self, *, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def execute(self, request):
        data = await request.tool.invoke(request.params or {}, request.context)
        return type(
            "Result",
            (),
            {
                "tool_id": request.tool_id,
                "success": True,
                "data": data,
                "error": None,
                "exception": None,
                "metadata": {"decorated": True},
            },
        )()

    async def execute_stream(self, request):
        yield {"type": "result", "data": {"decorated": True, "tool_id": request.tool_id}}


@context_assembler(name="decorated_context_assembler")
class DecoratorContextAssembler:
    def __init__(self, *, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def assemble(self, *, request, session_state, session_manager):
        _ = (request, session_state, session_manager)
        return ContextAssemblyResult(metadata={"decorated": True})

    async def finalize(self, *, request, session_state, session_manager, result):
        _ = (request, session_state, session_manager)
        return result
