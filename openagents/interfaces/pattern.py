"""Pattern plugin contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .plugin import BasePlugin

if TYPE_CHECKING:
    from .followup import FollowupResolverPlugin
    from .events import EventBusPlugin
    from .response_repair import ResponseRepairPolicyPlugin
    from .session import SessionArtifact
    from .runtime import RunArtifact, RunRequest, RunUsage
    from .tool import ExecutionPolicy, ToolExecutor


@dataclass
class ExecutionContext:
    """Execution context for pattern plugins."""

    agent_id: str
    session_id: str
    input_text: str
    state: dict[str, Any]
    tools: dict[str, Any]
    llm_client: Any | None
    llm_options: Any | None
    event_bus: "EventBusPlugin"
    memory_view: dict[str, Any] = field(default_factory=dict)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    scratch: dict[str, Any] = field(default_factory=dict)
    active_skill: str | None = None
    skill_metadata: dict[str, Any] = field(default_factory=dict)
    system_prompt_fragments: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    session_artifacts: list["SessionArtifact"] = field(default_factory=list)
    assembly_metadata: dict[str, Any] = field(default_factory=dict)
    run_request: "RunRequest | None" = None
    tool_executor: "ToolExecutor | None" = None
    execution_policy: "ExecutionPolicy | None" = None
    followup_resolver: "FollowupResolverPlugin | None" = None
    response_repair_policy: "ResponseRepairPolicyPlugin | None" = None
    usage: "RunUsage | None" = None
    artifacts: list["RunArtifact"] = field(default_factory=list)


class PatternPlugin(BasePlugin):
    """Base pattern plugin.

    Provides action methods (emit, call_tool, call_llm, compress_context)
    that can be customized by implementations to change runtime behavior.
    """

    context: ExecutionContext | None = None

    async def setup(
        self,
        agent_id: str,
        session_id: str,
        input_text: str,
        state: dict[str, Any],
        tools: dict[str, Any],
        llm_client: Any,
        llm_options: Any,
        event_bus: "EventBusPlugin",
        transcript: list[dict[str, Any]] | None = None,
        session_artifacts: list["SessionArtifact"] | None = None,
        assembly_metadata: dict[str, Any] | None = None,
        run_request: "RunRequest | None" = None,
        tool_executor: "ToolExecutor | None" = None,
        execution_policy: "ExecutionPolicy | None" = None,
        followup_resolver: "FollowupResolverPlugin | None" = None,
        response_repair_policy: "ResponseRepairPolicyPlugin | None" = None,
        usage: "RunUsage | None" = None,
        artifacts: list["RunArtifact"] | None = None,
    ) -> None:
        """Setup pattern with runtime data.

        Called by Runtime before execute() to initialize context.
        """
        self.context = ExecutionContext(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
            transcript=list(transcript or []),
            session_artifacts=list(session_artifacts or []),
            assembly_metadata=dict(assembly_metadata or {}),
            run_request=run_request,
            tool_executor=tool_executor,
            execution_policy=execution_policy,
            followup_resolver=followup_resolver,
            response_repair_policy=response_repair_policy,
            usage=usage,
            artifacts=artifacts or [],
        )

    async def execute(self) -> Any:
        """Execute pattern and return final result.

        The pattern should use self.call_tool(), self.call_llm(), etc.
        to interact with runtime.
        """
        raise NotImplementedError("PatternPlugin.execute must be implemented")

    async def react(self) -> dict[str, Any]:
        """Run one pattern step and return an action payload (legacy).

        Returns:
            Dict with action type and parameters (e.g., {"type": "tool_call", "tool": "...", "params": {...}})
        """
        raise NotImplementedError("PatternPlugin.react must be implemented")

    # Action methods - can be overridden by implementations

    async def emit(self, event_name: str, **payload: Any) -> None:
        """Emit an event.

        Default implementation delegates to event_bus.
        Override to customize event handling (e.g., filtering, transformation).
        """
        ctx = self.context
        await ctx.event_bus.emit(
            event_name,
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            **payload,
        )

    async def call_tool(
        self,
        tool_id: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool with retry and fallback support.

        Default implementation:
        - Try tool.invoke(params, self.context)
        - On exception, call tool.fallback(error, params, self.context)

        Override to customize tool calling (e.g., caching, retries, fallback).
        """
        ctx = self.context
        if tool_id not in ctx.tools:
            raise KeyError(f"Tool '{tool_id}' is not registered")
        tool = ctx.tools[tool_id]
        await self.emit("tool.called", tool_id=tool_id, params=params or {})
        try:
            result = await tool.invoke(params or {}, ctx)
            ctx.tool_results.append({"tool_id": tool_id, "result": result})
            if ctx.usage is not None:
                ctx.usage.tool_calls += 1
            await self.emit("tool.succeeded", tool_id=tool_id, result=result)
            return result
        except Exception as exc:
            await self.emit("tool.failed", tool_id=tool_id, error=str(exc))
            # Fallback: let tool handle retry
            result = await tool.fallback(exc, params or {}, ctx)
            if result is not None:  # Fallback provided new value
                return result
            raise  # Re-raise original for retry logic

    async def call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call LLM with streaming support.

        Default implementation delegates to llm_client.complete().
        Override to customize LLM calls (e.g., caching, fallback models).
        """
        ctx = self.context
        if ctx.llm_client is None:
            raise RuntimeError("No LLM client configured for this agent")
        await self.emit("llm.called", model=model)
        response = await ctx.llm_client.generate(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if ctx.usage is not None:
            ctx.usage.llm_calls += 1
            if response.usage is not None:
                ctx.usage.input_tokens += response.usage.input_tokens
                ctx.usage.output_tokens += response.usage.output_tokens
                ctx.usage.total_tokens += response.usage.total_tokens
        await self.emit("llm.succeeded", model=model)
        return response.output_text

    def compose_system_prompt(self, base_prompt: str) -> str:
        """Merge runtime/system fragments into one system prompt."""
        ctx = self.context
        fragments = [base_prompt.strip()]
        if ctx is not None:
            fragments.extend(
                fragment.strip()
                for fragment in ctx.system_prompt_fragments
                if isinstance(fragment, str) and fragment.strip()
            )
        return "\n\n".join(fragment for fragment in fragments if fragment)

    async def compress_context(self) -> None:
        """Compress context when it grows too large.

        Called to reduce context size (e.g., tool_results accumulated).
        Default implementation does nothing.

        Override to implement context compression (e.g., summarization, truncation).
        """
        pass

    def add_artifact(
        self,
        *,
        name: str,
        payload: Any,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a run artifact on the current execution context."""
        from .runtime import RunArtifact

        ctx = self.context
        ctx.artifacts.append(
            RunArtifact(
                name=name,
                kind=kind,
                payload=payload,
                metadata=dict(metadata or {}),
            )
        )
