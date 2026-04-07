"""Default runtime implementation - orchestrates agent execution."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    SKILL_CONTEXT_AUGMENT,
    SKILL_METADATA,
    SKILL_POST_RUN,
    SKILL_PRE_RUN,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOL_FILTER,
    supports,
)
from openagents.interfaces.events import (
    CONTEXT_CREATED,
    EventBusPlugin,
    MEMORY_INJECTED,
    MEMORY_INJECT_FAILED,
    MEMORY_WRITEBACK_FAILED,
    MEMORY_WRITEBACK_SUCCEEDED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_REQUESTED,
    RUN_VALIDATED,
    SESSION_ACQUIRED,
)
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import (
    RUN_STOP_COMPLETED,
    RUN_STOP_FAILED,
    RunRequest,
    RunResult,
    RunUsage,
    RUNTIME_RUN,
    RuntimePlugin,
)
from openagents.interfaces.session import SessionArtifact
from openagents.interfaces.tool import (
    ExecutionPolicy,
    PolicyDecision,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
)


def _supports_parameter(fn: Any, name: str) -> bool:
    params = inspect.signature(fn).parameters.values()
    return any(
        param.name == name or param.kind is inspect.Parameter.VAR_KEYWORD
        for param in params
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class _AllowAllExecutionPolicy:
    async def evaluate(self, request: ToolExecutionRequest) -> PolicyDecision:
        return PolicyDecision(allowed=True)


class _DefaultToolExecutor:
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        policy = getattr(request.context, "execution_policy", None)
        decision = PolicyDecision(allowed=True)
        if policy is not None and hasattr(policy, "evaluate"):
            decision = await policy.evaluate(request)
        if not decision.allowed:
            exc = PermissionError(decision.reason or f"Tool '{request.tool_id}' denied by policy")
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(exc),
                exception=exc,
                metadata={"policy": dict(decision.metadata)},
            )

        try:
            data = await request.tool.invoke(request.params or {}, request.context)
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=True,
                data=data,
                metadata={"policy": dict(decision.metadata)},
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(exc),
                exception=exc,
                metadata={"policy": dict(decision.metadata)},
            )

    async def execute_stream(self, request: ToolExecutionRequest):
        policy = getattr(request.context, "execution_policy", None)
        decision = PolicyDecision(allowed=True)
        if policy is not None and hasattr(policy, "evaluate"):
            decision = await policy.evaluate(request)
        if not decision.allowed:
            raise PermissionError(decision.reason or f"Tool '{request.tool_id}' denied by policy")

        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk


class _BoundTool:
    def __init__(self, *, tool_id: str, tool: Any, executor: _DefaultToolExecutor):
        self._tool_id = tool_id
        self._tool = tool
        self._executor = executor

    def execution_spec(self) -> ToolExecutionSpec:
        get_spec = getattr(self._tool, "execution_spec", None)
        if callable(get_spec):
            return get_spec()
        return ToolExecutionSpec()

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        result = await self._executor.execute(
            ToolExecutionRequest(
                tool_id=self._tool_id,
                tool=self._tool,
                params=params or {},
                context=context,
                execution_spec=self.execution_spec(),
                metadata={"bound_tool": True},
            )
        )
        if result.success:
            return result.data
        if result.exception is not None:
            raise result.exception
        raise RuntimeError(result.error or f"Tool '{self._tool_id}' failed")

    async def invoke_stream(self, params: dict[str, Any], context: Any):
        request = ToolExecutionRequest(
            tool_id=self._tool_id,
            tool=self._tool,
            params=params or {},
            context=context,
            execution_spec=self.execution_spec(),
            metadata={"bound_tool": True},
        )
        async for chunk in self._executor.execute_stream(request):
            yield chunk

    async def fallback(self, error: Exception, params: dict[str, Any], context: Any) -> Any:
        fallback = getattr(self._tool, "fallback", None)
        if callable(fallback):
            return await fallback(error, params, context)
        raise error

    def describe(self) -> dict[str, Any]:
        describe = getattr(self._tool, "describe", None)
        if callable(describe):
            return describe()
        return {"name": self._tool_id, "description": "", "parameters": {"type": "object"}}

    def schema(self) -> dict[str, Any]:
        schema = getattr(self._tool, "schema", None)
        if callable(schema):
            return schema()
        return {"type": "object", "properties": {}, "required": []}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)


class DefaultRuntime(RuntimePlugin):
    """Default runtime implementation.

    Orchestrates agent execution with:
    - Session isolation and locking
    - Event lifecycle management
    - Memory inject/execute/writeback flow
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(
            config=config or {},
            capabilities={RUNTIME_RUN},
        )
        self._event_bus: EventBusPlugin | None = None
        self._session_manager: Any | None = None
        self._llm_clients: dict[str, Any | None] = {}

    @property
    def event_bus(self) -> EventBusPlugin:
        if self._event_bus is None:
            raise RuntimeError("EventBus not initialized. Call load_runtime_components() first.")
        return self._event_bus

    @property
    def session_manager(self) -> Any:
        if self._session_manager is None:
            raise RuntimeError("SessionManager not initialized. Call load_runtime_components() first.")
        return self._session_manager

    async def run(
        self,
        *,
        request: RunRequest,
        app_config: "AppConfig",
        agents_by_id: dict[str, "AgentDefinition"],
        agent_plugins: Any = None,
    ) -> RunResult:
        """Execute an agent run."""
        from openagents.plugins.loader import load_agent_plugins

        agent = agents_by_id.get(request.agent_id)
        if agent is None:
            raise ValueError(f"Unknown agent id: '{request.agent_id}'")

        if agent_plugins is None:
            plugins = load_agent_plugins(agent)
        else:
            plugins = agent_plugins

        await self._event_bus.emit(
            RUN_REQUESTED,
            agent_id=request.agent_id,
            session_id=request.session_id,
            input_text=request.input_text,
            run_id=request.run_id,
        )

        llm_client = self._get_llm_client(agent)
        await self._event_bus.emit(
            RUN_VALIDATED,
            agent_id=request.agent_id,
            session_id=request.session_id,
            run_id=request.run_id,
        )

        usage = RunUsage()
        artifacts = []
        execution_policy = _AllowAllExecutionPolicy()
        tool_executor = _DefaultToolExecutor()

        try:
            async with self._session_manager.session(request.session_id) as session_state:
                await self._event_bus.emit(
                    SESSION_ACQUIRED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                session_state.pop("_runtime_last_output", None)
                transcript = await self._load_transcript(request.session_id)
                self._apply_runtime_budget(pattern=plugins.pattern, agent=agent)
                bound_tools = self._bind_tools(plugins.tools, tool_executor)

                await self._setup_pattern(
                    pattern=plugins.pattern,
                    request=request,
                    state=session_state,
                    tools=bound_tools,
                    llm_client=llm_client,
                    llm_options=agent.llm,
                    transcript=transcript,
                    tool_executor=tool_executor,
                    execution_policy=execution_policy,
                    usage=usage,
                    artifacts=artifacts,
                )

                await self._event_bus.emit(
                    CONTEXT_CREATED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                await self._apply_skill(pattern=plugins.pattern, skill=plugins.skill)
                await self._run_memory_inject(agent=agent, memory=plugins.memory, pattern=plugins.pattern)
                await self._apply_skill_runtime_hooks(pattern=plugins.pattern, skill=plugins.skill)
                await self._run_skill_pre_run(pattern=plugins.pattern, skill=plugins.skill)
                result = await plugins.pattern.execute()
                result = await self._run_skill_post_run(
                    pattern=plugins.pattern,
                    skill=plugins.skill,
                    result=result,
                )
                await self._run_memory_writeback(agent=agent, memory=plugins.memory, pattern=plugins.pattern)
                session_state["_runtime_last_output"] = result
                await self._append_transcript(
                    request=request,
                    final_output=result,
                    stop_reason=RUN_STOP_COMPLETED,
                )
                await self._persist_artifacts(request.session_id, artifacts)

                await self._event_bus.emit(
                    RUN_COMPLETED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    result=result,
                )
                return RunResult(
                    run_id=request.run_id,
                    final_output=result,
                    stop_reason=RUN_STOP_COMPLETED,
                    usage=usage,
                    artifacts=list(artifacts),
                    metadata={
                        "agent_id": request.agent_id,
                        "session_id": request.session_id,
                    },
                )
        except Exception as exc:
            await self._append_transcript(
                request=request,
                final_output=str(exc),
                stop_reason=RUN_STOP_FAILED,
                is_error=True,
            )
            await self._event_bus.emit(
                RUN_FAILED,
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                error=str(exc),
            )
            return RunResult(
                run_id=request.run_id,
                stop_reason=RUN_STOP_FAILED,
                usage=usage,
                artifacts=list(artifacts),
                error=str(exc),
                exception=exc,
                metadata={
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                },
            )

    def _get_llm_client(self, agent: "AgentDefinition") -> Any | None:
        if agent.id in self._llm_clients:
            return self._llm_clients[agent.id]
        from openagents.llm.registry import create_llm_client

        client = create_llm_client(agent.llm)
        self._llm_clients[agent.id] = client
        return client

    def invalidate_llm_client(self, agent_id: str | None = None) -> None:
        """Drop cached LLM clients so updated config is used on next run."""
        if agent_id is None:
            self._llm_clients.clear()
            return
        self._llm_clients.pop(agent_id, None)

    def _apply_runtime_budget(self, *, pattern: PatternPlugin, agent: "AgentDefinition") -> None:
        config = getattr(pattern, "config", None)
        if not isinstance(config, dict):
            config = {}
        config.setdefault("max_steps", agent.runtime.max_steps)
        config.setdefault("step_timeout_ms", agent.runtime.step_timeout_ms)
        setattr(pattern, "config", config)
        current_max_steps = getattr(pattern, "_max_steps", None)
        if current_max_steps is not None and not callable(current_max_steps):
            setattr(pattern, "_max_steps", agent.runtime.max_steps)
        current_step_timeout = getattr(pattern, "_step_timeout_ms", None)
        if current_step_timeout is not None and not callable(current_step_timeout):
            setattr(pattern, "_step_timeout_ms", agent.runtime.step_timeout_ms)

    def _bind_tools(
        self,
        tools: dict[str, Any],
        executor: _DefaultToolExecutor,
    ) -> dict[str, Any]:
        return {
            tool_id: _BoundTool(tool_id=tool_id, tool=tool, executor=executor)
            for tool_id, tool in tools.items()
        }

    async def _setup_pattern(
        self,
        *,
        pattern: PatternPlugin,
        request: RunRequest,
        state: dict[str, Any],
        tools: dict[str, Any],
        llm_client: Any | None,
        llm_options: Any | None,
        transcript: list[dict[str, Any]],
        tool_executor: _DefaultToolExecutor,
        execution_policy: ExecutionPolicy,
        usage: RunUsage,
        artifacts: list[Any],
    ) -> None:
        setup_kwargs = {
            "agent_id": request.agent_id,
            "session_id": request.session_id,
            "input_text": request.input_text,
            "state": state,
            "tools": tools,
            "llm_client": llm_client,
            "llm_options": llm_options,
            "event_bus": self._event_bus,
        }
        optional = {
            "transcript": transcript,
            "run_request": request,
            "tool_executor": tool_executor,
            "execution_policy": execution_policy,
            "usage": usage,
            "artifacts": artifacts,
        }
        for name, value in optional.items():
            if _supports_parameter(pattern.setup, name):
                setup_kwargs[name] = value

        await pattern.setup(**setup_kwargs)
        context = getattr(pattern, "context", None)
        if context is None:
            return
        context.state = state
        context.tools = tools
        context.llm_client = llm_client
        context.llm_options = llm_options
        context.event_bus = self._event_bus
        context.transcript = list(transcript)
        context.run_request = request
        context.tool_executor = tool_executor
        context.execution_policy = execution_policy
        context.usage = usage
        context.artifacts = artifacts

    async def _load_transcript(self, session_id: str) -> list[dict[str, Any]]:
        loader = getattr(self._session_manager, "load_messages", None)
        if callable(loader):
            return await loader(session_id)
        return []

    async def _append_transcript(
        self,
        *,
        request: RunRequest,
        final_output: Any,
        stop_reason: str,
        is_error: bool = False,
    ) -> None:
        append_message = getattr(self._session_manager, "append_message", None)
        if not callable(append_message):
            return
        await append_message(
            request.session_id,
            {
                "role": "user",
                "content": request.input_text,
                "run_id": request.run_id,
                "agent_id": request.agent_id,
            },
        )
        await append_message(
            request.session_id,
            {
                "role": "assistant",
                "content": final_output,
                "run_id": request.run_id,
                "agent_id": request.agent_id,
                "stop_reason": stop_reason,
                "is_error": is_error,
            },
        )

    async def _persist_artifacts(self, session_id: str, artifacts: list[Any]) -> None:
        save_artifact = getattr(self._session_manager, "save_artifact", None)
        if not callable(save_artifact):
            return
        for artifact in artifacts:
            await save_artifact(
                session_id,
                SessionArtifact(
                    name=getattr(artifact, "name", "artifact"),
                    kind=getattr(artifact, "kind", "generic"),
                    payload=getattr(artifact, "payload", None),
                    metadata=dict(getattr(artifact, "metadata", {})),
                ),
            )

    async def _apply_skill(self, *, pattern: PatternPlugin, skill: Any | None) -> None:
        if skill is None or pattern.context is None:
            return

        context = pattern.context
        context.active_skill = getattr(skill, "name", "") or type(skill).__name__

        if supports(skill, SKILL_SYSTEM_PROMPT):
            prompt = await _maybe_await(skill.get_system_prompt(context))
            if isinstance(prompt, str) and prompt.strip():
                context.system_prompt_fragments.append(prompt.strip())

        if supports(skill, SKILL_METADATA):
            metadata = await _maybe_await(skill.get_metadata())
            if isinstance(metadata, dict):
                context.skill_metadata.update(metadata)

    async def _apply_skill_runtime_hooks(self, *, pattern: PatternPlugin, skill: Any | None) -> None:
        if skill is None or pattern.context is None:
            return

        context = pattern.context
        if supports(skill, SKILL_CONTEXT_AUGMENT):
            await _maybe_await(skill.augment_context(context))

        if supports(skill, SKILL_TOOL_FILTER):
            filtered = await _maybe_await(skill.filter_tools(dict(context.tools), context))
            if filtered is None:
                return
            if not isinstance(filtered, dict):
                raise TypeError("skill.filter_tools() must return a dict[str, Any] or None")
            context.tools = filtered

    async def _run_skill_pre_run(self, *, pattern: PatternPlugin, skill: Any | None) -> None:
        if skill is None or pattern.context is None:
            return
        if supports(skill, SKILL_PRE_RUN):
            await _maybe_await(skill.before_run(pattern.context))

    async def _run_skill_post_run(
        self,
        *,
        pattern: PatternPlugin,
        skill: Any | None,
        result: Any,
    ) -> Any:
        if skill is None or pattern.context is None:
            return result
        if not supports(skill, SKILL_POST_RUN):
            return result
        updated = await _maybe_await(skill.after_run(pattern.context, result))
        return result if updated is None else updated

    async def _run_memory_inject(
        self,
        *,
        agent: AgentDefinition,
        memory: Any,
        pattern: PatternPlugin,
    ) -> None:
        if not supports(memory, MEMORY_INJECT):
            return
        context = pattern.context
        try:
            await memory.inject(context)
            await self._event_bus.emit(
                MEMORY_INJECTED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_INJECT_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            if agent.memory.on_error == "fail":
                raise

    async def _run_memory_writeback(
        self,
        *,
        agent: AgentDefinition,
        memory: Any,
        pattern: PatternPlugin,
    ) -> None:
        if not supports(memory, MEMORY_WRITEBACK):
            return
        context = pattern.context
        try:
            await memory.writeback(context)
            await self._event_bus.emit(
                MEMORY_WRITEBACK_SUCCEEDED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_WRITEBACK_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            if agent.memory.on_error == "fail":
                raise
