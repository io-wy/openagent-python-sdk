"""Default runtime implementation - orchestrates agent execution."""

from __future__ import annotations

import importlib
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openagents.config.schema import AgentDefinition, AppConfig

from pydantic import BaseModel

from openagents.errors.exceptions import (
    BudgetExhausted,
    MaxStepsExceeded,
    ModelRetryError,
    OpenAgentsError,
    OutputValidationError,
    PatternError,
    PermanentToolError,
)
from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    supports,
)
from openagents.interfaces.context import ContextAssemblerPlugin, ContextAssemblyResult
from openagents.interfaces.events import (
    CONTEXT_CREATED,
    MEMORY_INJECT_FAILED,
    MEMORY_INJECTED,
    MEMORY_WRITEBACK_FAILED,
    MEMORY_WRITEBACK_SUCCEEDED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_REQUESTED,
    RUN_VALIDATED,
    SESSION_ACQUIRED,
    EventBusPlugin,
)
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import (
    RUN_STOP_COMPLETED,
    RUN_STOP_FAILED,
    RUN_STOP_TIMEOUT,
    RUNTIME_RUN,
    RunRequest,
    RunResult,
    RuntimePlugin,
    RunUsage,
    StopReason,
)
from openagents.interfaces.session import SessionArtifact
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
    ToolExecutor,
    ToolExecutorPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin

logger = logging.getLogger("openagents")


def _supports_parameter(fn: Any, name: str) -> bool:
    params = inspect.signature(fn).parameters.values()
    return any(param.name == name or param.kind is inspect.Parameter.VAR_KEYWORD for param in params)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _import_symbol(path: str) -> Any:
    if "." not in path:
        raise ValueError(f"Invalid impl path: '{path}'")
    module_name, attr_name = path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _instantiate(factory: Any, config: dict[str, Any]) -> Any:
    if not callable(factory):
        return factory
    try:
        return factory(config=config)
    except TypeError as exc:
        raise TypeError(f"Could not instantiate runtime dependency from {factory!r}: {exc}") from exc


class _DefaultToolExecutor(ToolExecutorPlugin):
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        try:
            data = await request.tool.invoke(request.params or {}, request.context)
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=True,
                data=data,
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=str(exc),
                exception=exc,
            )

    async def execute_stream(self, request: ToolExecutionRequest):
        async for chunk in request.tool.invoke_stream(request.params or {}, request.context):
            yield chunk


class _DefaultContextAssembler(ContextAssemblerPlugin):
    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        transcript: list[dict[str, Any]] = []
        load_messages = getattr(session_manager, "load_messages", None)
        if callable(load_messages):
            transcript = await load_messages(request.session_id)

        session_artifacts = []
        list_artifacts = getattr(session_manager, "list_artifacts", None)
        if callable(list_artifacts):
            session_artifacts = await list_artifacts(request.session_id)

        return ContextAssemblyResult(
            transcript=transcript,
            session_artifacts=session_artifacts,
        )

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        return result


class _BoundTool:
    def __init__(
        self,
        *,
        tool_id: str,
        tool: Any,
        executor: ToolExecutor,
    ):
        self._tool_id = tool_id
        self._tool = tool
        self._executor = executor

    def execution_spec(self) -> ToolExecutionSpec:
        get_spec = getattr(self._tool, "execution_spec", None)
        if callable(get_spec):
            return get_spec()
        return ToolExecutionSpec()

    async def invoke(self, params: dict[str, Any], context: Any) -> ToolExecutionResult:
        """Return the full :class:`ToolExecutionResult` so executor metadata
        (retry counts, timeouts, policy decisions) survives to events.

        The base :class:`PatternPlugin.call_tool` unwraps via
        :func:`unwrap_tool_result` for backward-compatible data access
        and propagates ``executor_metadata`` on the ``tool.succeeded``
        event payload.

        Policy evaluation is now owned by the executor (see
        :meth:`ToolExecutorPlugin.evaluate_policy`); the bound tool no
        longer performs a pre-dispatch policy check.
        """
        budget = getattr(getattr(context, "run_request", None), "budget", None)
        usage = getattr(context, "usage", None)
        if budget is not None and budget.max_tool_calls is not None and usage is not None:
            if usage.tool_calls >= budget.max_tool_calls:
                raise MaxStepsExceeded(f"Tool call limit ({budget.max_tool_calls}) exceeded").with_context(
                    agent_id=getattr(context, "agent_id", None),
                    session_id=getattr(context, "session_id", None),
                    run_id=getattr(getattr(context, "run_request", None), "run_id", None),
                    tool_id=self._tool_id,
                )
        request = ToolExecutionRequest(
            tool_id=self._tool_id,
            tool=self._tool,
            params=params or {},
            context=context,
            execution_spec=self.execution_spec(),
            metadata={"bound_tool": True},
        )
        result = await self._executor.execute(request)
        if result.success:
            usage = getattr(context, "usage", None)
            if usage is not None:
                usage.tool_calls += 1
            return result
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


class DefaultRuntime(TypedConfigPluginMixin, RuntimePlugin):
    """Default runtime implementation.

    What:
        Owns the per-run orchestration: acquires the session lock,
        runs ``context_assembler.assemble``, rebinds tools through the
        ``tool_executor`` (which owns policy evaluation internally),
        then drives ``pattern.setup`` / ``memory.inject`` /
        ``pattern.execute`` / ``memory.writeback``, finally persists
        the transcript and artifacts. Emits the full set of lifecycle
        events declared in
        :data:`openagents.interfaces.event_taxonomy.EVENT_SCHEMAS`.

    Usage:
        ``{"runtime": {"type": "default"}}``. Optional
        per-dependency overrides under ``config.tool_executor`` and
        ``config.context_assembler``.

    Depends on:
        - ``EventBusPlugin`` (top-level ``events``) for emit
        - ``SessionManagerPlugin`` (top-level ``session``) for state
          and transcript persistence
        - the agent's ``memory`` / ``pattern`` / optional executor
          plugins
    """

    class Config(BaseModel):
        tool_executor: dict[str, Any] | None = None
        context_assembler: dict[str, Any] | None = None

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(
            config=config or {},
            capabilities={RUNTIME_RUN},
        )
        self._init_typed_config()
        self._event_bus: EventBusPlugin | None = None
        self._session_manager: Any | None = None
        self._llm_clients: dict[str, Any | None] = {}
        self._tool_executor: ToolExecutor | None = None
        self._context_assembler: ContextAssemblerPlugin | None = None

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
            from openagents.errors.suggestions import near_match

            available = sorted(agents_by_id.keys())
            guess = near_match(request.agent_id, available)
            extra = f" Did you mean '{guess}'?" if guess else ""
            raise ValueError(f"Unknown agent id: '{request.agent_id}'.{extra} Available: {available}")

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
        await self._event_bus.emit(
            "session.run.started",
            agent_id=request.agent_id,
            session_id=request.session_id,
            run_id=request.run_id,
            input_text=request.input_text,
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
        tool_executor = self._resolve_tool_executor(agent_plugins)
        context_assembler = self._resolve_context_assembler(agent_plugins)
        started_at = time.perf_counter()

        try:
            async with self._session_manager.session(request.session_id) as session_state:
                await self._event_bus.emit(
                    SESSION_ACQUIRED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                session_state.pop("_runtime_last_output", None)
                await self._event_bus.emit("context.assemble.started")
                assemble_started_at = time.perf_counter()
                assembly = await context_assembler.assemble(
                    request=request,
                    session_state=session_state,
                    session_manager=self._session_manager,
                )
                assemble_duration_ms = int((time.perf_counter() - assemble_started_at) * 1000)
                await self._event_bus.emit(
                    "context.assemble.completed",
                    transcript_size=len(assembly.transcript),
                    artifact_count=len(assembly.session_artifacts),
                    duration_ms=assemble_duration_ms,
                )
                self._apply_runtime_budget(pattern=plugins.pattern, agent=agent)
                bound_tools = self._bind_tools(plugins.tools, tool_executor)

                await self._run_tool_preflight(
                    tools=plugins.tools,
                    request=request,
                )

                await self._setup_pattern(
                    pattern=plugins.pattern,
                    request=request,
                    state=session_state,
                    tools=bound_tools,
                    llm_client=llm_client,
                    llm_options=agent.llm,
                    transcript=assembly.transcript,
                    session_artifacts=assembly.session_artifacts,
                    assembly_metadata=assembly.metadata,
                    tool_executor=tool_executor,
                    usage=usage,
                    artifacts=artifacts,
                )

                await self._event_bus.emit(
                    CONTEXT_CREATED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                self._enforce_duration_budget(request=request, started_at=started_at)
                await self._run_memory_inject(agent=agent, memory=plugins.memory, pattern=plugins.pattern)
                self._enforce_duration_budget(request=request, started_at=started_at)

                output_type = request.output_type
                max_retries = (
                    request.budget.max_validation_retries
                    if request.budget is not None and request.budget.max_validation_retries is not None
                    else 3
                )
                attempts = 0
                raw = await plugins.pattern.execute()
                self._enforce_duration_budget(request=request, started_at=started_at)
                validation_exhausted: OutputValidationError | None = None

                finalize_fn = getattr(plugins.pattern, "finalize", None)
                if finalize_fn is None:
                    # Duck-typed pattern without finalize hook: skip validation entirely.
                    result = raw
                    validation_exhausted = None
                    # Fall through past the while loop (no retries needed).
                    finalize_enabled = False
                else:
                    finalize_enabled = True

                while finalize_enabled:
                    try:
                        result = await finalize_fn(raw, output_type)
                        break
                    except ModelRetryError as retry_exc:
                        attempts += 1
                        if max_retries is not None and attempts > max_retries:
                            validation_exhausted = OutputValidationError(
                                str(retry_exc),
                                output_type=output_type,
                                attempts=attempts,
                                last_validation_error=retry_exc.validation_error,
                            ).with_context(
                                agent_id=request.agent_id,
                                session_id=request.session_id,
                                run_id=request.run_id,
                            )
                            break
                        pattern_ctx = getattr(plugins.pattern, "context", None)
                        if pattern_ctx is not None:
                            pattern_ctx.scratch["last_validation_error"] = {
                                "attempt": attempts,
                                "message": str(retry_exc),
                                "expected_schema": (output_type.model_json_schema() if output_type is not None else {}),
                            }
                        await self._event_bus.emit(
                            "validation.retry",
                            agent_id=request.agent_id,
                            session_id=request.session_id,
                            run_id=request.run_id,
                            attempt=attempts,
                            error=str(retry_exc),
                        )
                        raw = await plugins.pattern.execute()
                        self._enforce_duration_budget(request=request, started_at=started_at)

                if validation_exhausted is not None:
                    await self._append_transcript(
                        request=request,
                        final_output=str(validation_exhausted),
                        stop_reason=RUN_STOP_FAILED,
                        is_error=True,
                    )
                    await self._persist_artifacts(request.session_id, artifacts)
                    await self._event_bus.emit(
                        RUN_FAILED,
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                        run_id=request.run_id,
                        error=str(validation_exhausted),
                    )
                    await self._event_bus.emit(
                        "session.run.completed",
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                        run_id=request.run_id,
                        stop_reason=RUN_STOP_FAILED,
                        duration_ms=int((time.perf_counter() - started_at) * 1000),
                    )
                    return RunResult(
                        run_id=request.run_id,
                        final_output=None,
                        stop_reason=RUN_STOP_FAILED,
                        usage=usage,
                        artifacts=list(artifacts),
                        exception=validation_exhausted,
                        error=str(validation_exhausted),
                        metadata={
                            "agent_id": request.agent_id,
                            "session_id": request.session_id,
                        },
                    )

                self._enforce_duration_budget(request=request, started_at=started_at)
                await self._run_memory_writeback(agent=agent, memory=plugins.memory, pattern=plugins.pattern)
                self._enforce_duration_budget(request=request, started_at=started_at)
                session_state["_runtime_last_output"] = result
                await self._append_transcript(
                    request=request,
                    final_output=result,
                    stop_reason=RUN_STOP_COMPLETED,
                )
                await self._persist_artifacts(request.session_id, artifacts)

                run_result = RunResult(
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
                finalized_result = await context_assembler.finalize(
                    request=request,
                    session_state=session_state,
                    session_manager=self._session_manager,
                    result=run_result,
                )
                if finalized_result is not None:
                    run_result = finalized_result

                await self._event_bus.emit(
                    RUN_COMPLETED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    result=result,
                )
                await self._event_bus.emit(
                    "session.run.completed",
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    stop_reason=RUN_STOP_COMPLETED,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
                return run_result
        except Exception as exc:
            wrapped_exc = exc
            if not isinstance(exc, OpenAgentsError):
                wrapped_exc = PatternError(str(exc)).with_context(
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )
            stop_reason = RUN_STOP_FAILED
            if isinstance(wrapped_exc, MaxStepsExceeded):
                stop_reason = StopReason.MAX_STEPS.value
            elif isinstance(wrapped_exc, BudgetExhausted):
                stop_reason = StopReason.BUDGET_EXHAUSTED.value
            elif isinstance(exc, TimeoutError):
                stop_reason = RUN_STOP_TIMEOUT
            await self._append_transcript(
                request=request,
                final_output=str(wrapped_exc),
                stop_reason=stop_reason,
                is_error=True,
            )
            await self._event_bus.emit(
                RUN_FAILED,
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                error=str(wrapped_exc),
            )
            await self._event_bus.emit(
                "session.run.completed",
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                stop_reason=stop_reason,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            run_result = RunResult(
                run_id=request.run_id,
                stop_reason=stop_reason,
                usage=usage,
                artifacts=list(artifacts),
                error=str(wrapped_exc),
                exception=wrapped_exc,
                metadata={
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                },
            )
            finalized_result = await context_assembler.finalize(
                request=request,
                session_state=session_state if "session_state" in locals() else {},
                session_manager=self._session_manager,
                result=run_result,
            )
            if finalized_result is not None:
                run_result = finalized_result
            return run_result

    def _get_llm_client(self, agent: "AgentDefinition") -> Any | None:
        if agent.id in self._llm_clients:
            return self._llm_clients[agent.id]
        from openagents.llm.registry import create_llm_client

        client = create_llm_client(agent.llm)
        self._llm_clients[agent.id] = client
        return client

    async def _close_llm_client(self, client: Any | None) -> None:
        if client is None:
            return
        close = getattr(client, "aclose", None)
        if callable(close):
            await close()

    async def invalidate_llm_client(self, agent_id: str | None = None) -> None:
        """Drop cached LLM clients so updated config is used on next run."""
        if agent_id is None:
            clients = list(self._llm_clients.values())
            self._llm_clients.clear()
            for client in clients:
                await self._close_llm_client(client)
            return
        client = self._llm_clients.pop(agent_id, None)
        await self._close_llm_client(client)

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
        executor: ToolExecutor,
    ) -> dict[str, Any]:
        return {tool_id: _BoundTool(tool_id=tool_id, tool=tool, executor=executor) for tool_id, tool in tools.items()}

    async def _run_tool_preflight(
        self,
        *,
        tools: dict[str, Any],
        request: RunRequest,
    ) -> None:
        for tool_id, tool in tools.items():
            preflight = getattr(tool, "preflight", None)
            if not callable(preflight):
                continue
            started = time.perf_counter()
            try:
                await preflight(None)
            except PermanentToolError as exc:
                msg = str(exc.args[0]) if exc.args else ""
                if f"[tool:{tool_id}]" not in msg:
                    prefixed = PermanentToolError(
                        f"[tool:{tool_id}] {msg}" if msg else f"[tool:{tool_id}] preflight failed",
                        tool_name=tool_id,
                        hint=exc.hint,
                        docs_url=exc.docs_url,
                    )
                    prefixed.with_context(
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                        run_id=request.run_id,
                    )
                    propagate: PermanentToolError = prefixed
                else:
                    exc.tool_id = exc.tool_id or tool_id
                    exc.with_context(
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                        run_id=request.run_id,
                    )
                    propagate = exc
                await self._event_bus.emit(
                    "tool.preflight",
                    tool_id=tool_id,
                    result="error",
                    error=str(propagate),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                raise propagate from exc
            await self._event_bus.emit(
                "tool.preflight",
                tool_id=tool_id,
                result="ok",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def _enforce_duration_budget(self, *, request: RunRequest, started_at: float) -> None:
        budget = request.budget
        if budget is None or budget.max_duration_ms is None:
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if elapsed_ms > budget.max_duration_ms:
            raise BudgetExhausted(f"Run duration limit ({budget.max_duration_ms}ms) exceeded").with_context(
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
            )

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
        session_artifacts: list[Any],
        assembly_metadata: dict[str, Any],
        tool_executor: ToolExecutor,
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
            "session_artifacts": session_artifacts,
            "assembly_metadata": assembly_metadata,
            "run_request": request,
            "tool_executor": tool_executor,
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
        context.session_artifacts = list(session_artifacts)
        context.assembly_metadata = dict(assembly_metadata)
        context.run_request = request
        context.deps = request.deps
        context.tool_executor = tool_executor
        context.usage = usage
        context.artifacts = artifacts

    def _get_tool_executor(self) -> ToolExecutor:
        from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor

        if self._tool_executor is not None:
            return self._tool_executor
        self._tool_executor = self._load_runtime_dependency(
            key="tool_executor",
            default_factory=_DefaultToolExecutor,
            builtin_factories={"default": _DefaultToolExecutor, "safe": SafeToolExecutor},
            required_methods=("execute", "execute_stream"),
        )
        return self._tool_executor

    def _resolve_tool_executor(self, agent_plugins: Any) -> ToolExecutor:
        tool_executor = getattr(agent_plugins, "tool_executor", None)
        if tool_executor is not None:
            self._bind_runtime_dependency(tool_executor)
            return tool_executor
        return self._get_tool_executor()

    def _get_context_assembler(self) -> ContextAssemblerPlugin:
        from openagents.plugins.builtin.context.truncating import TruncatingContextAssembler

        if self._context_assembler is not None:
            return self._context_assembler
        self._context_assembler = self._load_runtime_dependency(
            key="context_assembler",
            default_factory=_DefaultContextAssembler,
            builtin_factories={
                "default": _DefaultContextAssembler,
                "truncating": TruncatingContextAssembler,
            },
            required_methods=("assemble", "finalize"),
        )
        return self._context_assembler

    def _resolve_context_assembler(self, agent_plugins: Any) -> ContextAssemblerPlugin:
        context_assembler = getattr(agent_plugins, "context_assembler", None)
        if context_assembler is not None:
            self._bind_runtime_dependency(context_assembler)
            return context_assembler
        return self._get_context_assembler()

    def _load_runtime_dependency(
        self,
        *,
        key: str,
        default_factory: Any,
        builtin_factories: dict[str, Any],
        required_methods: tuple[str, ...],
    ) -> Any:
        # Read through self.cfg so unknown top-level runtime config keys are
        # surfaced via TypedConfigPluginMixin's warning.
        raw = getattr(self.cfg, key, None)
        if raw is None:
            dependency = default_factory()
            self._bind_runtime_dependency(dependency)
            return dependency
        if not isinstance(raw, dict):
            raise TypeError(f"runtime.config.{key} must be an object")

        dep_type = raw.get("type")
        dep_impl = raw.get("impl")
        dep_config = raw.get("config", {})
        if dep_config is None:
            dep_config = {}
        if not isinstance(dep_config, dict):
            raise TypeError(f"runtime.config.{key}.config must be an object")

        if isinstance(dep_impl, str) and dep_impl.strip():
            factory = _import_symbol(dep_impl.strip())
        elif isinstance(dep_type, str) and dep_type.strip():
            factory = builtin_factories.get(dep_type.strip())
            if factory is None:
                raise ValueError(
                    f"Unknown runtime.config.{key}.type '{dep_type.strip()}'. Available: {sorted(builtin_factories)}"
                )
        else:
            raise ValueError(f"runtime.config.{key} must set one of 'type' or 'impl'")

        dependency = _instantiate(factory, dep_config)
        for method_name in required_methods:
            if not callable(getattr(dependency, method_name, None)):
                raise TypeError(
                    f"runtime.config.{key} dependency '{type(dependency).__name__}' must implement '{method_name}'"
                )
        self._bind_runtime_dependency(dependency)
        return dependency

    def _bind_runtime_dependency(self, dependency: Any) -> None:
        if hasattr(dependency, "_event_bus"):
            dependency._event_bus = self._event_bus
        if hasattr(dependency, "_session_manager"):
            dependency._session_manager = self._session_manager
        if hasattr(dependency, "event_bus"):
            try:
                dependency.event_bus = self._event_bus
            except Exception:
                pass
        if hasattr(dependency, "session_manager"):
            try:
                dependency.session_manager = self._session_manager
            except Exception:
                pass

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
        await self._event_bus.emit("memory.inject.started")
        try:
            await memory.inject(context)
            view = getattr(context, "memory_view", None)
            view_size = len(view) if view is not None else 0
            await self._event_bus.emit(
                MEMORY_INJECTED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
            await self._event_bus.emit(
                "memory.inject.completed",
                view_size=view_size,
            )
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_INJECT_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            logger.warning(
                "Memory %s failed during inject (on_error=%s): %s",
                type(memory).__name__,
                agent.memory.on_error,
                exc,
                exc_info=True,
                extra={
                    "agent_id": context.agent_id,
                    "session_id": context.session_id,
                    "run_id": getattr(getattr(context, "run_request", None), "run_id", None),
                },
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
        await self._event_bus.emit("memory.writeback.started")
        try:
            await memory.writeback(context)
            await self._event_bus.emit(
                MEMORY_WRITEBACK_SUCCEEDED,
                agent_id=context.agent_id,
                session_id=context.session_id,
            )
            await self._event_bus.emit("memory.writeback.completed")
        except Exception as exc:
            await self._event_bus.emit(
                MEMORY_WRITEBACK_FAILED,
                agent_id=context.agent_id,
                session_id=context.session_id,
                error=str(exc),
            )
            logger.warning(
                "Memory %s failed during writeback (on_error=%s): %s",
                type(memory).__name__,
                agent.memory.on_error,
                exc,
                exc_info=True,
                extra={
                    "agent_id": context.agent_id,
                    "session_id": context.session_id,
                    "run_id": getattr(getattr(context, "run_request", None), "run_id", None),
                },
            )
            if agent.memory.on_error == "fail":
                raise

    async def close(self) -> None:
        clients = list(self._llm_clients.values())
        self._llm_clients.clear()
        for client in clients:
            await self._close_llm_client(client)
