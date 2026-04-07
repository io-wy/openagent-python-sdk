from __future__ import annotations

import asyncio
from typing import Any

from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    PATTERN_EXECUTE,
    PATTERN_REACT,
    SKILL_CONTEXT_AUGMENT,
    SKILL_METADATA,
    SKILL_POST_RUN,
    SKILL_PRE_RUN,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOL_FILTER,
    SKILL_TOOLS,
)
from openagents.interfaces.runtime import RunArtifact


class InjectWritebackMemory:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {MEMORY_INJECT, MEMORY_WRITEBACK}

    async def inject(self, context: Any) -> None:
        context.state["memory_injected"] = True

    async def writeback(self, context: Any) -> None:
        context.state["memory_written"] = True


class FailingInjectMemory:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {MEMORY_INJECT}

    async def inject(self, context: Any) -> None:
        raise RuntimeError("inject failed")


class FinalPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        injected = self.context.state.get("memory_injected", False)
        return {"type": "final", "content": f"injected={injected}"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class SlowFinalPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        delay = float(self.config.get("delay", 0.05))
        await asyncio.sleep(delay)
        return {"type": "final", "content": "slow-done"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


# Validation helpers - extracted from ReActPattern for reuse
def _validate_action(action: Any, action_type: str | None = None) -> dict[str, Any]:
    """Validate action format and type."""
    if not isinstance(action, dict):
        raise TypeError(f"Pattern action must be dict, got {type(action).__name__}")

    action_type = action.get("type")
    if not isinstance(action_type, str) or not action_type.strip():
        raise ValueError("Pattern action must include a non-empty string 'type'")

    allowed = {"tool_call", "final", "continue"}
    if action_type not in allowed:
        raise ValueError(
            f"Unsupported pattern action type: '{action_type}'. "
            f"Allowed: {sorted(allowed)}"
        )

    if action_type == "tool_call":
        tool_id = action.get("tool") or action.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError("tool_call action must include non-empty 'tool' or 'tool_id'")
        params = action.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("tool_call action 'params' must be an object")

    return action


class NonDictActionPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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

    async def react(self) -> Any:
        return "not-a-dict-action"

    async def execute(self) -> Any:
        action = await self.react()
        _validate_action(action)
        return action.get("content")


class UnknownTypePattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "unknown_type"}

    async def execute(self) -> Any:
        action = await self.react()
        _validate_action(action)
        return action.get("content")


class MissingToolCallFieldPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "tool_call", "params": {"query": "x"}}

    async def execute(self) -> Any:
        action = await self.react()
        _validate_action(action)
        return action.get("content")


class InvalidToolCallParamsPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "tool_call", "tool": "search", "params": "not-an-object"}

    async def execute(self) -> Any:
        action = await self.react()
        _validate_action(action)
        return action.get("content")


class ContinueForeverPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self._max_steps = config.get("max_steps", 4) if config else 4
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "continue"}

    async def execute(self) -> Any:
        max_steps = self._max_steps
        for step in range(max_steps):
            action = await self.react()
            _validate_action(action)
            if action.get("type") == "final":
                return action.get("content")
        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")


class SlowContinuePattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self._max_steps = config.get("max_steps", 4) if config else 4
        self._step_timeout_ms = config.get("step_timeout_ms", 1000) if config else 1000
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        delay = float(self.config.get("delay", 0.1))
        await asyncio.sleep(delay)
        return {"type": "continue"}

    async def execute(self) -> Any:
        max_steps = self._max_steps
        timeout_s = self._step_timeout_ms / 1000
        for step in range(max_steps):
            try:
                action = await asyncio.wait_for(self.react(), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Pattern step timed out after {self._step_timeout_ms}ms at step {step}"
                ) from exc
            _validate_action(action)
            if action.get("type") == "final":
                return action.get("content")
        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")


class FailOnceThenFinalPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        if not self.context.state.get("failed_once"):
            self.context.state["failed_once"] = True
            raise RuntimeError("pattern fail once")
        return {"type": "final", "content": "recovered"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class PromptAwarePattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "final", "content": "prompt-aware"}

    async def execute(self) -> Any:
        return {
            "active_skill": self.context.active_skill,
            "prompt": list(self.context.system_prompt_fragments),
            "metadata": dict(self.context.skill_metadata),
            "tools": sorted(self.context.tools.keys()),
        }


class ArtifactPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id: str, session_id: str, input_text: str, state: dict[str, Any], tools: dict[str, Any], llm_client: Any, llm_options: Any, event_bus: Any) -> None:
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
        return {"type": "final", "content": "artifact-done"}

    async def execute(self) -> Any:
        self.context.artifacts.append(
            RunArtifact(
                name="report.txt",
                kind="text",
                payload="artifact payload",
                metadata={"source": "ArtifactPattern"},
            )
        )
        return "artifact-done"


class RuntimePromptSkill:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {SKILL_SYSTEM_PROMPT, SKILL_METADATA}

    def get_system_prompt(self, context: Any | None = None) -> str:
        focus = self.config.get("focus", "training")
        return f"You are the {focus} specialist."

    def get_metadata(self) -> dict[str, Any]:
        return {"focus": self.config.get("focus", "training")}


class RuntimeLifecycleSkill:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {
            SKILL_SYSTEM_PROMPT,
            SKILL_METADATA,
            SKILL_TOOLS,
            SKILL_CONTEXT_AUGMENT,
            SKILL_TOOL_FILTER,
            SKILL_PRE_RUN,
            SKILL_POST_RUN,
        }

    def get_system_prompt(self, context: Any | None = None) -> str:
        return "You are the lifecycle specialist."

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {"id": "skill_calc", "type": "calc"},
            {"id": "search", "type": "builtin_search"},
        ]

    def get_metadata(self) -> dict[str, Any]:
        return {"focus": self.config.get("focus", "lifecycle")}

    def augment_context(self, context: Any) -> None:
        context.memory_view["skill_augmented"] = True
        context.state["skill_context_augmented"] = True

    def filter_tools(
        self,
        tools: dict[str, Any],
        context: Any | None = None,
    ) -> dict[str, Any]:
        return {
            tool_id: tool
            for tool_id, tool in tools.items()
            if tool_id != "search"
        }

    async def before_run(self, context: Any) -> None:
        context.state["skill_pre_run"] = True

    async def after_run(self, context: Any, result: Any) -> Any:
        context.state["skill_post_run"] = True
        if not isinstance(result, dict):
            return result
        updated = dict(result)
        updated["memory_view"] = dict(context.memory_view)
        updated["state"] = {
            "skill_context_augmented": context.state.get("skill_context_augmented"),
            "skill_pre_run": context.state.get("skill_pre_run"),
            "skill_post_run": context.state.get("skill_post_run"),
        }
        return updated
