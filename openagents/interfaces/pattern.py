"""Pattern plugin contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .plugin import BasePlugin
from .run_context import RunContext

if TYPE_CHECKING:
    from .followup import FollowupResolverPlugin
    from .events import EventBusPlugin
    from .response_repair import ResponseRepairPolicyPlugin
    from .session import SessionArtifact
    from .runtime import RunArtifact, RunRequest, RunUsage
    from .tool import ExecutionPolicy, ToolExecutor


ExecutionContext = RunContext[Any]


class PatternPlugin(BasePlugin):
    """Base pattern plugin.

    Provides action methods (emit, call_tool, call_llm, compress_context)
    that can be customized by implementations to change runtime behavior.
    """

    context: RunContext[Any] | None = None

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
        self.context = RunContext[Any](
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_request.run_id if run_request is not None else "",
            input_text=input_text,
            deps=getattr(run_request, "deps", None),
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
        """Execute pattern and return final result."""
        raise NotImplementedError("PatternPlugin.execute must be implemented")

    async def react(self) -> dict[str, Any]:
        """Run one pattern step and return an action payload (legacy)."""
        raise NotImplementedError("PatternPlugin.react must be implemented")

    async def emit(self, event_name: str, **payload: Any) -> None:
        """Emit an event."""
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
        """Call a tool with retry and fallback support."""
        ctx = self.context
        if tool_id not in ctx.tools:
            raise KeyError(f"Tool '{tool_id}' is not registered")
        tool = ctx.tools[tool_id]
        await self.emit("tool.called", tool_id=tool_id, params=params or {})
        before_tool_calls = ctx.usage.tool_calls if ctx.usage is not None else None
        try:
            result = await tool.invoke(params or {}, ctx)
            ctx.tool_results.append({"tool_id": tool_id, "result": result})
            if (
                ctx.usage is not None
                and before_tool_calls is not None
                and ctx.usage.tool_calls == before_tool_calls
            ):
                ctx.usage.tool_calls += 1
            await self.emit("tool.succeeded", tool_id=tool_id, result=result)
            return result
        except Exception as exc:
            await self.emit("tool.failed", tool_id=tool_id, error=str(exc))
            result = await tool.fallback(exc, params or {}, ctx)
            if result is not None:
                return result
            raise

    async def call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
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

                meta = response.usage.metadata or {}
                cached_read = int(meta.get("cache_read_input_tokens", meta.get("cached_tokens", 0)) or 0)
                cached_write = int(meta.get("cache_creation_input_tokens", 0) or 0)
                ctx.usage.input_tokens_cached += cached_read
                ctx.usage.input_tokens_cache_creation += cached_write

                call_cost = meta.get("cost_usd")
                sticky = ctx.scratch.get("__cost_unavailable__")
                if sticky or call_cost is None:
                    ctx.usage.cost_usd = None
                    ctx.scratch["__cost_unavailable__"] = True
                else:
                    current = ctx.usage.cost_usd if ctx.usage.cost_usd is not None else 0.0
                    ctx.usage.cost_usd = current + float(call_cost)
                    for bucket, amount in (meta.get("cost_breakdown") or {}).items():
                        ctx.usage.cost_breakdown[bucket] = ctx.usage.cost_breakdown.get(bucket, 0.0) + float(amount)
        await self.emit("usage.updated", usage=ctx.usage.model_dump() if ctx.usage else None)
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
        """Compress context when it grows too large."""
        pass

    def add_artifact(
        self,
        *,
        name: str,
        payload: Any,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a runtime artifact to the current context."""
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
