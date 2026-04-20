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
from openagents.interfaces.context import ContextAssemblyResult
from openagents.interfaces.runtime import RunArtifact
from openagents.interfaces.session import SessionArtifact
from openagents.interfaces.tool import PolicyDecision, ToolExecutionResult


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


class FailingWritebackMemory:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {MEMORY_INJECT, MEMORY_WRITEBACK}

    async def inject(self, context: Any) -> None:
        context.state["memory_injected"] = True

    async def writeback(self, context: Any) -> None:
        raise RuntimeError("writeback failed")


class FinalPattern:
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
        injected = self.context.state.get("memory_injected", False)
        return {"type": "final", "content": f"injected={injected}"}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class DepsEchoPattern:
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
            run_id="fixture-run",
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
        )

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": self.context.deps}

    async def execute(self) -> Any:
        action = await self.react()
        return action.get("content")


class SlowFinalPattern:
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
        raise ValueError(f"Unsupported pattern action type: '{action_type}'. Allowed: {sorted(allowed)}")

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
                raise TimeoutError(f"Pattern step timed out after {self._step_timeout_ms}ms at step {step}") from exc
            _validate_action(action)
            if action.get("type") == "final":
                return action.get("content")
        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")


class FailOnceThenFinalPattern:
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


class ToolCallingPattern:
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
        return {"type": "tool_call", "tool": "custom_tool", "params": {"value": self.context.input_text}}

    async def execute(self) -> Any:
        from openagents.interfaces.pattern import unwrap_tool_result

        tool = self.context.tools["custom_tool"]
        raw = await tool.invoke({"value": self.context.input_text}, self.context)
        data, _ = unwrap_tool_result(raw)
        return data


class TwoToolCallsPattern:
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
        return {"type": "continue"}

    async def execute(self) -> Any:
        from openagents.interfaces.pattern import unwrap_tool_result

        tool = self.context.tools["custom_tool"]
        first_raw = await tool.invoke({"value": "one"}, self.context)
        second_raw = await tool.invoke({"value": "two"}, self.context)
        first, _ = unwrap_tool_result(first_raw)
        second, _ = unwrap_tool_result(second_raw)
        return {"first": first, "second": second}


class ContextAwarePattern:
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
        return {"type": "final", "content": "context-aware"}

    async def execute(self) -> Any:
        return {
            "transcript_count": len(self.context.transcript),
            "artifact_names": [artifact.name for artifact in self.context.session_artifacts],
            "assembly_metadata": dict(self.context.assembly_metadata),
            "state": {
                "assembler_seen": self.context.state.get("assembler_seen"),
                "assembler_finalized": self.context.state.get("assembler_finalized"),
            },
        }


class ConfigurableToolPattern:
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
        return {"type": "continue"}

    async def execute(self) -> Any:
        from openagents.interfaces.pattern import unwrap_tool_result

        tool_id = self.config.get("tool_id", "custom_tool")
        params = dict(self.config.get("params", {}))
        raw = await self.context.tools[tool_id].invoke(params, self.context)
        data, _ = unwrap_tool_result(raw)
        return data


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
        return {tool_id: tool for tool_id, tool in tools.items() if tool_id != "search"}

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


class PrefixingToolExecutor:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def execute(self, request: Any) -> ToolExecutionResult:
        data = await request.tool.invoke(request.params or {}, request.context)
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=True,
            data={
                "executor": self.config.get("name", "prefixed"),
                "data": data,
            },
        )

    async def execute_stream(self, request: Any):
        yield {
            "type": "result",
            "data": {
                "executor": self.config.get("name", "prefixed"),
                "tool_id": request.tool_id,
            },
        }


class DenyToolExecutionPolicy:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._deny_tools = set(self.config.get("deny_tools", []))

    async def evaluate(self, request: Any) -> PolicyDecision:
        if request.tool_id in self._deny_tools:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{request.tool_id}' blocked by DenyToolExecutionPolicy",
            )
        return PolicyDecision(allowed=True, metadata={"policy": "custom"})


class DenyingToolExecutor:
    """ToolExecutor whose ``evaluate_policy`` denies listed tools.

    Demonstrates the new pattern for tool-level restrictions after the
    ``execution_policy`` seam was folded into
    ``ToolExecutorPlugin.evaluate_policy``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._deny_tools = set(self.config.get("deny_tools", []))

    async def evaluate_policy(self, request: Any) -> PolicyDecision:
        if request.tool_id in self._deny_tools:
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{request.tool_id}' blocked by DenyingToolExecutor",
            )
        return PolicyDecision(allowed=True, metadata={"policy": "denying_executor"})

    async def execute(self, request: Any) -> ToolExecutionResult:
        from openagents.errors.exceptions import ToolError

        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=decision.reason,
                exception=ToolError(decision.reason, tool_name=request.tool_id),
                metadata={"policy": decision.metadata},
            )
        data = await request.tool.invoke(request.params or {}, request.context)
        return ToolExecutionResult(
            tool_id=request.tool_id,
            success=True,
            data=data,
        )

    async def execute_stream(self, request: Any):
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            yield {"type": "error", "error": decision.reason}
            return
        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk


class SummarizingContextAssembler:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> Any:
        transcript = await session_manager.load_messages(request.session_id)
        artifacts = await session_manager.list_artifacts(request.session_id)
        session_state["assembler_seen"] = True
        prefix = self.config.get("prefix", "summary")
        return ContextAssemblyResult(
            transcript=list(transcript) + [{"role": "system", "content": f"{prefix}:{request.input_text}"}],
            session_artifacts=list(artifacts),
            metadata={"assembler": prefix},
        )

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        session_state["assembler_finalized"] = True
        await session_manager.save_artifact(
            request.session_id,
            SessionArtifact(
                name="assembly-summary.txt",
                kind="text",
                payload=f"stop={getattr(result, 'stop_reason', 'unknown')}",
            ),
        )
        return result


class BadContextAssembler:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}


from openagents.interfaces.pattern import PatternPlugin as _PatternPlugin  # noqa: E402


class QueuedRawOutputPattern(_PatternPlugin):
    """Pattern fixture that returns a queue of raw outputs on successive execute() calls.

    Inherits ``PatternPlugin`` so it picks up ``finalize()``,
    ``_format_validation_error()``, and ``_inject_validation_correction()``.
    Each ``execute()`` call pops one item from the config-provided
    ``responses`` list and returns it as raw output, allowing the validation
    retry loop to drive multiple attempts.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        from openagents.interfaces.capabilities import PATTERN_EXECUTE

        super().__init__(config=config or {}, capabilities={PATTERN_EXECUTE})
        self._responses = list(self.config.get("responses", []))
        self.execute_calls = 0

    async def execute(self) -> Any:
        # Apply any queued validation correction from prior failed finalize.
        self._inject_validation_correction()
        self.execute_calls += 1
        if not self._responses:
            raise RuntimeError("QueuedRawOutputPattern exhausted its response queue")
        return self._responses.pop(0)

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": await self.execute()}
