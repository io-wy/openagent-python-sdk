from __future__ import annotations

from typing import Any

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


class BadSkillMissingContextAugmentMethod:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {SKILL_CONTEXT_AUGMENT}
