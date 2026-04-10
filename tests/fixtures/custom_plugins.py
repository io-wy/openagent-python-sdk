from __future__ import annotations

import asyncio
from typing import Any

from openagents.interfaces.context import ContextAssemblyResult
from openagents.interfaces.followup import FollowupResolution
from openagents.interfaces.response_repair import ResponseRepairDecision
from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    PATTERN_EXECUTE,
    PATTERN_REACT,
    SKILL_CONTEXT_AUGMENT,
    SKILL_METADATA,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOLS,
    TOOL_INVOKE,
)


class CustomMemory:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {MEMORY_INJECT}

    async def inject(self, context: Any) -> None:
        return None


class CustomPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
        """Setup pattern with runtime data."""
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
        return {"type": "final", "content": "ok"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class CustomTool:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {TOOL_INVOKE}

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        return {"ok": True, "params": params}


class SlowTool:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {TOOL_INVOKE}
        self._delay = float(self.config.get("delay", 0.05))

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        await asyncio.sleep(self._delay)
        return {"ok": True, "params": params}


class BadPatternNoCapability:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = set()

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
        pass

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": "bad"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class CustomSkill:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {SKILL_SYSTEM_PROMPT, SKILL_TOOLS, SKILL_METADATA}

    def get_system_prompt(self, context: Any | None = None) -> str:
        focus = self.config.get("focus", "general")
        return f"Focus on {focus} analysis."

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {"id": "skill_calc", "type": "calc"},
            {"id": "search", "type": "builtin_search"},
        ]

    def get_metadata(self) -> dict[str, Any]:
        return {"focus": self.config.get("focus", "general")}


class BadSkillNoCapability:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = set()


class CustomToolExecutor:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def execute(self, request: Any) -> Any:
        data = await request.tool.invoke(request.params or {}, request.context)
        return type("Result", (), {
            "tool_id": request.tool_id,
            "success": True,
            "data": {"executor": self.config.get("name", "custom"), "data": data},
            "error": None,
            "exception": None,
            "metadata": {},
        })()

    async def execute_stream(self, request: Any):
        yield {"type": "result", "data": {"executor": self.config.get("name", "custom")}}


class CustomExecutionPolicy:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._deny_tools = set(self.config.get("deny_tools", []))

    async def evaluate(self, request: Any) -> Any:
        allowed = request.tool_id not in self._deny_tools
        return type("Decision", (), {
            "allowed": allowed,
            "reason": "" if allowed else f"Denied {request.tool_id}",
            "metadata": {},
        })()


class CustomContextAssembler:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        session_state["custom_assembler_seen"] = True
        return ContextAssemblyResult(
            transcript=[{"role": "system", "content": self.config.get("marker", "assembled")}],
            metadata={"marker": self.config.get("marker", "assembled")},
        )

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        session_state["custom_assembler_finalized"] = True
        return result


class CustomFollowupResolver:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def resolve(self, context: Any) -> Any:
        target = str(self.config.get("when_input", ""))
        if target and str(context.input_text).strip() == target:
            return FollowupResolution(resolved=True, output=self.config.get("result", "resolved"))
        return None


class CustomResponseRepairPolicy:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def repair_empty_response(
        self,
        *,
        context: Any,
        messages: list[dict[str, Any]],
        assistant_content: list[dict[str, Any]],
        stop_reason: str | None,
        retries: int,
    ) -> Any:
        _ = (context, messages, assistant_content, stop_reason, retries)
        return ResponseRepairDecision(handled=True, output=self.config.get("result", "repaired"))


class BadSkillMissingContextAugmentMethod:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {SKILL_CONTEXT_AUGMENT}
