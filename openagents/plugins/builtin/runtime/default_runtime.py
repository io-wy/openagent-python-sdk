"""Default runtime implementation - orchestrates agent execution."""

from __future__ import annotations

import importlib
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from openagents.config.schema import AgentDefinition, AppConfig

from pydantic import BaseModel, Field

from openagents.errors.exceptions import (
    BudgetExhausted,
    ConfigError,
    LLMConnectionError,
    LLMRateLimitError,
    MaxStepsExceeded,
    ModelRetryError,
    OpenAgentsError,
    OutputValidationError,
    PatternError,
    PermanentToolError,
    ToolRateLimitError,
    ToolUnavailableError,
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
from openagents.interfaces.session import SessionArtifact, SessionCheckpoint
from openagents.interfaces.tool import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionSpec,
    ToolExecutor,
    ToolExecutorPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin

logger = logging.getLogger("openagents")

# Errors classified as retryable by the durable-run resume loop.
# Any other OpenAgentsError subclass (PermanentToolError, ConfigError,
# BudgetExhausted, OutputValidationError, PatternError, ModelRetryError
# post-budget) is treated as permanent.
RETRYABLE_RUN_ERRORS: tuple[type[OpenAgentsError], ...] = (
    LLMRateLimitError,
    LLMConnectionError,
    ToolRateLimitError,
    ToolUnavailableError,
)

# State-dict key used by the durable-run machinery to stash usage /
# artifacts / step counter across checkpoints. Private to this module.
_DURABLE_STATE_KEY = "__durable__"


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

    @property
    def durable_idempotent(self) -> bool:
        return bool(getattr(self._tool, "durable_idempotent", True))

    async def invoke(self, params: dict[str, Any], context: Any) -> ToolExecutionResult:
        """Bound invocation: call_id + approval gate + before/after hooks + executor.

        Owns the per-call lifecycle: assigns ``call_id`` and stashes it in
        ``ctx.scratch['__current_call_id__']``; evaluates
        ``tool.requires_approval()`` and consults
        ``ctx.run_request.context_hints['approvals']``; runs
        ``before_invoke`` and ``after_invoke`` hooks around the executor.

        Returns the executor's :class:`ToolExecutionResult` on success so
        metadata (retry counts, timeouts, policy decisions) flows to events.
        Raises on failure so the pattern fallback path still works.
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

        call_id = uuid4().hex
        scratch = getattr(context, "scratch", None)
        if isinstance(scratch, dict):
            scratch["__current_call_id__"] = call_id

        # One-shot durable idempotency warning: emitted the first time a
        # tool declaring durable_idempotent=False is invoked inside a
        # durable run. Dedup key = (run_id, tool_id). Advisory only — we
        # do not block the call.
        run_request = getattr(context, "run_request", None)
        if (
            run_request is not None
            and getattr(run_request, "durable", False)
            and not self.durable_idempotent
            and isinstance(scratch, dict)
        ):
            warned: set[str] = scratch.setdefault("__idempotency_warned__", set())
            if self._tool_id not in warned:
                warned.add(self._tool_id)
                event_bus = getattr(context, "event_bus", None)
                if event_bus is not None and callable(getattr(event_bus, "emit", None)):
                    try:
                        await event_bus.emit(
                            "run.durable_idempotency_warning",
                            run_id=getattr(run_request, "run_id", ""),
                            tool_id=self._tool_id,
                            hint=(
                                f"Tool '{self._tool_id}' declares durable_idempotent=False; "
                                "on resume it may re-execute and repeat side effects."
                            ),
                        )
                    except Exception:  # noqa: BLE001
                        pass

        if self._requires_approval(params, context):
            event_bus = getattr(context, "event_bus", None)
            if event_bus is not None and callable(getattr(event_bus, "emit", None)):
                try:
                    await event_bus.emit(
                        "tool.approval_needed",
                        tool_id=self._tool_id,
                        call_id=call_id,
                        params=params or {},
                    )
                except Exception:
                    pass
            approvals = self._approvals_dict(context)
            decision = approvals.get(call_id) if approvals else None
            if decision is None and approvals:
                decision = approvals.get("*")
            if decision is None:
                raise PermanentToolError(
                    f"Tool '{self._tool_id}' requires approval; no decision found for call_id '{call_id}'",
                    tool_name=self._tool_id,
                    hint=f"Inject context_hints['approvals']['{call_id}'] = 'allow' and re-run",
                )
            if decision == "deny":
                raise PermanentToolError(
                    f"Tool '{self._tool_id}' denied by approval policy",
                    tool_name=self._tool_id,
                )

        before = getattr(self._tool, "before_invoke", None)
        if callable(before):
            await before(params or {}, context)

        cancel_event = scratch.get("__cancel_event__") if isinstance(scratch, dict) else None
        request = ToolExecutionRequest(
            tool_id=self._tool_id,
            tool=self._tool,
            params=params or {},
            context=context,
            execution_spec=self.execution_spec(),
            metadata={"bound_tool": True, "call_id": call_id},
            cancel_event=cancel_event,
        )
        exception: BaseException | None = None
        result: ToolExecutionResult | None = None
        try:
            result = await self._executor.execute(request)
            if result.success:
                if usage is not None:
                    usage.tool_calls += 1
                return result
            exception = (
                result.exception
                if result.exception is not None
                else RuntimeError(result.error or f"Tool '{self._tool_id}' failed")
            )
            raise exception
        except BaseException as exc:
            if exception is None:
                exception = exc
            raise
        finally:
            after = getattr(self._tool, "after_invoke", None)
            if callable(after):
                try:
                    await after(
                        params or {},
                        context,
                        result.data if (result is not None and result.success) else None,
                        exception,
                    )
                except Exception:
                    # after_invoke must not mask the original exception path.
                    pass

    def _requires_approval(self, params: dict[str, Any], context: Any) -> bool:
        check = getattr(self._tool, "requires_approval", None)
        if not callable(check):
            return False
        try:
            return bool(check(params or {}, context))
        except Exception:
            return False

    def _approvals_dict(self, context: Any) -> dict[str, str] | None:
        run_request = getattr(context, "run_request", None)
        if run_request is None:
            return None
        hints = getattr(run_request, "context_hints", None)
        if not isinstance(hints, dict):
            return None
        approvals = hints.get("approvals")
        return approvals if isinstance(approvals, dict) else None

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

    async def invoke_batch(self, items, context):
        """Dispatch a batch through the executor (executor.execute_batch or sequential fallback).

        Preserves the input ``item_id`` on each ``BatchResult`` and input order.
        """
        from openagents.interfaces.tool import BatchResult

        if not items:
            return []

        scratch = getattr(context, "scratch", None)
        cancel_event = scratch.get("__cancel_event__") if isinstance(scratch, dict) else None
        spec = self.execution_spec()
        requests = [
            ToolExecutionRequest(
                tool_id=self._tool_id,
                tool=self._tool,
                params=it.params or {},
                context=context,
                execution_spec=spec,
                metadata={"bound_tool": True, "batch_item_id": it.item_id},
                cancel_event=cancel_event,
            )
            for it in items
        ]
        batch_method = getattr(self._executor, "execute_batch", None)
        if callable(batch_method):
            results = await batch_method(requests)
        else:
            results = [await self._executor.execute(r) for r in requests]

        out: list[BatchResult] = []
        for item, res in zip(items, results):
            if res.success:
                out.append(BatchResult(item_id=item.item_id, success=True, data=res.data))
            else:
                out.append(
                    BatchResult(
                        item_id=item.item_id,
                        success=False,
                        error=res.error,
                        exception=res.exception,
                    )
                )
        return out

    async def invoke_background(self, params, context):
        """Submit a long-running job via the wrapped tool.

        Background jobs bypass the executor cancel/timeout race — their lifecycle
        is owned by the tool implementation. ``before_invoke`` / ``after_invoke``
        still run so hook-based instrumentation works.
        """
        before = getattr(self._tool, "before_invoke", None)
        if callable(before):
            await before(params or {}, context)
        handle = None
        exception: BaseException | None = None
        try:
            handle = await self._tool.invoke_background(params or {}, context)
            event_bus = getattr(context, "event_bus", None)
            if event_bus is not None and callable(getattr(event_bus, "emit", None)):
                try:
                    scratch = getattr(context, "scratch", None)
                    call_id = scratch.get("__current_call_id__") if isinstance(scratch, dict) else None
                    await event_bus.emit(
                        "tool.background.submitted",
                        tool_id=self._tool_id,
                        call_id=call_id or handle.job_id,
                        job_id=handle.job_id,
                    )
                except Exception:
                    pass
            return handle
        except BaseException as exc:
            exception = exc
            raise
        finally:
            after = getattr(self._tool, "after_invoke", None)
            if callable(after):
                try:
                    await after(params or {}, context, handle, exception)
                except Exception:
                    pass

    async def poll_job(self, handle, context):
        return await self._tool.poll_job(handle, context)

    async def cancel_job(self, handle, context):
        return await self._tool.cancel_job(handle, context)

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

    class McpRuntimeConfig(BaseModel):
        """Runtime-wide knobs for the shared MCP session pool (Phase 2/3).

        All fields are optional; defaults preserve today's behaviour
        (no cross-run pool, no eviction).
        """

        max_pooled_sessions: int | None = None
        max_idle_seconds: float | None = None
        preflight_cache_success_ttl: float | None = None

    class Config(BaseModel):
        tool_executor: dict[str, Any] | None = None
        context_assembler: dict[str, Any] | None = None
        mcp: "DefaultRuntime.McpRuntimeConfig" = Field(default_factory=lambda: DefaultRuntime.McpRuntimeConfig())

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
        self._diagnostics: Any | None = None
        self._llm_clients: dict[str, Any | None] = {}
        self._tool_executor: ToolExecutor | None = None
        self._context_assembler: ContextAssemblerPlugin | None = None
        from ._mcp_coordinator import _McpSessionCoordinator

        mcp_cfg = self.cfg.mcp
        self._mcp_coordinator = _McpSessionCoordinator(
            max_pooled_sessions=mcp_cfg.max_pooled_sessions,
            max_idle_seconds=mcp_cfg.max_idle_seconds,
        )

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

        # Diagnostics wiring: record metrics from pattern.call_llm and
        # maintain a tool-call chain that capture_error_snapshot can read.
        diag = self._diagnostics
        diag_tool_chain: list[dict[str, Any]] = []
        diag_llm_handler = None
        diag_llm_fail_handler = None
        diag_tool_called_handler = None
        if diag is not None:
            from openagents.interfaces.diagnostics import LLMCallMetrics as _LLMCallMetrics

            def _diag_llm_handler(event):
                metrics = (event.payload or {}).get("_metrics")
                if isinstance(metrics, _LLMCallMetrics):
                    diag.record_llm_call(request.run_id, metrics)

            def _diag_tool_called_handler(event):
                payload = event.payload or {}
                entry = {
                    "tool_id": payload.get("tool_id"),
                    "params": payload.get("params"),
                }
                call_id = payload.get("call_id")
                if call_id is not None:
                    entry["call_id"] = call_id
                diag_tool_chain.append(entry)

            diag_llm_handler = _diag_llm_handler
            diag_llm_fail_handler = _diag_llm_handler
            diag_tool_called_handler = _diag_tool_called_handler
            self._event_bus.subscribe("llm.succeeded", diag_llm_handler)
            self._event_bus.subscribe("llm.failed", diag_llm_fail_handler)
            self._event_bus.subscribe("tool.called", diag_tool_called_handler)

        resumed_from_checkpoint: SessionCheckpoint | None = None
        try:
            async with self._session_manager.session(request.session_id) as session_state:
                await self._event_bus.emit(
                    SESSION_ACQUIRED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                session_state.pop("_runtime_last_output", None)

                # --- Explicit resume entry point ---
                # When request.resume_from_checkpoint is set, we skip the
                # normal context_assembler.assemble() path and build the
                # assembly from the loaded checkpoint's state instead.
                if request.resume_from_checkpoint:
                    checkpoint = await self._session_manager.load_checkpoint(
                        request.session_id, request.resume_from_checkpoint
                    )
                    if checkpoint is None:
                        available = await self._list_checkpoint_ids(request.session_id)
                        hint = (
                            f"No checkpoint '{request.resume_from_checkpoint}' for session "
                            f"'{request.session_id}'. Available: {available}"
                            if available
                            else f"No checkpoints exist for session '{request.session_id}'."
                        )
                        raise ConfigError(
                            f"Unknown resume_from_checkpoint '{request.resume_from_checkpoint}'",
                            hint=hint,
                        )
                    resumed_from_checkpoint = checkpoint
                    ckpt_state = dict(checkpoint.state or {})
                    transcript_full = list(ckpt_state.get("_session_transcript", []))
                    if checkpoint.transcript_length and checkpoint.transcript_length <= len(transcript_full):
                        transcript_full = transcript_full[: checkpoint.transcript_length]
                    ckpt_artifacts_raw = list(ckpt_state.get("_session_artifacts", []))
                    if checkpoint.artifact_count and checkpoint.artifact_count <= len(ckpt_artifacts_raw):
                        ckpt_artifacts_raw = ckpt_artifacts_raw[: checkpoint.artifact_count]
                    ckpt_artifacts = [
                        SessionArtifact.from_dict(item) if isinstance(item, dict) else item
                        for item in ckpt_artifacts_raw
                    ]
                    assembly = ContextAssemblyResult(
                        transcript=transcript_full,
                        session_artifacts=ckpt_artifacts,
                        metadata={"resumed_from_checkpoint": request.resume_from_checkpoint},
                    )
                    # Merge the checkpoint's saved state (minus private keys) into session_state
                    # so subsequent pattern.execute() sees the same scratch data it had.
                    for key, value in ckpt_state.items():
                        if key.startswith("_session_"):
                            continue
                        session_state[key] = value
                    # Rehydrate usage / artifacts from the durable blob (if any).
                    durable_blob = ckpt_state.get(_DURABLE_STATE_KEY) or {}
                    blob_usage = durable_blob.get("usage")
                    if isinstance(blob_usage, dict):
                        try:
                            restored = RunUsage.model_validate(blob_usage)
                            usage.llm_calls = restored.llm_calls
                            usage.tool_calls = restored.tool_calls
                            usage.input_tokens = restored.input_tokens
                            usage.output_tokens = restored.output_tokens
                            usage.total_tokens = restored.total_tokens
                            usage.input_tokens_cached = restored.input_tokens_cached
                            usage.input_tokens_cache_creation = restored.input_tokens_cache_creation
                            usage.cost_usd = restored.cost_usd
                            usage.cost_breakdown = dict(restored.cost_breakdown)
                        except Exception:  # noqa: BLE001
                            logger.exception("failed to rehydrate usage from resume checkpoint")
                    from openagents.interfaces.runtime import RunArtifact as _RunArtifact

                    blob_artifacts = durable_blob.get("artifacts")
                    if isinstance(blob_artifacts, list):
                        for item in blob_artifacts:
                            try:
                                if isinstance(item, dict):
                                    artifacts.append(_RunArtifact.model_validate(item))
                            except Exception:  # noqa: BLE001
                                logger.exception("failed to rehydrate artifact on resume")
                    await self._event_bus.emit(
                        "context.assemble.completed",
                        transcript_size=len(assembly.transcript),
                        artifact_count=len(assembly.session_artifacts),
                        duration_ms=0,
                    )
                else:
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

                mcp_pool = await self._mcp_coordinator.get_or_create(request.session_id)

                await self._run_tool_preflight(
                    tools=plugins.tools,
                    request=request,
                    mcp_pool=mcp_pool,
                )

                await self._mcp_coordinator.warmup_eager(mcp_pool, plugins.tools.values())

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

                # Seed a per-run cancel event so tools can race against external
                # cancellation. External callers set the event to request cancel.
                import asyncio as _asyncio

                pattern_ctx = getattr(plugins.pattern, "context", None)
                if pattern_ctx is not None and isinstance(getattr(pattern_ctx, "scratch", None), dict):
                    pattern_ctx.scratch.setdefault("__cancel_event__", _asyncio.Event())
                    pattern_ctx.scratch["__mcp_session_pool__"] = mcp_pool
                    if diag is not None:
                        # Expose the tool-call chain on the pattern's RunContext so
                        # DiagnosticsPlugin.capture_error_snapshot can read it if
                        # an exception fires before we return.
                        pattern_ctx.scratch["_diag_tool_chain"] = diag_tool_chain

                await self._event_bus.emit(
                    CONTEXT_CREATED,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                )

                self._enforce_duration_budget(request=request, started_at=started_at)
                # Skip memory.inject on explicit resume — the checkpoint's
                # transcript already contains the injected memory from the
                # original run, and re-injecting is not idempotent for
                # memory plugins with side effects.
                if resumed_from_checkpoint is None:
                    await self._run_memory_inject(agent=agent, memory=plugins.memory, pattern=plugins.pattern)
                self._enforce_duration_budget(request=request, started_at=started_at)

                # --- Durable step-checkpoint subscription (opt-in) ---
                checkpoint_handler = None
                if request.durable:
                    checkpoint_handler = self._build_step_checkpoint_handler(
                        run_id=request.run_id,
                        session_id=request.session_id,
                        session_state=session_state,
                        pattern=plugins.pattern,
                        usage=usage,
                        artifacts=artifacts,
                    )
                    self._event_bus.subscribe("tool.succeeded", checkpoint_handler)
                    self._event_bus.subscribe("llm.succeeded", checkpoint_handler)

                output_type = request.output_type
                max_retries = (
                    request.budget.max_validation_retries
                    if request.budget is not None and request.budget.max_validation_retries is not None
                    else 3
                )
                max_resume = (
                    request.budget.max_resume_attempts
                    if request.budget is not None and request.budget.max_resume_attempts is not None
                    else 3
                )
                resume_attempt = 0
                attempts = 0
                validation_exhausted: OutputValidationError | None = None
                result: Any = None

                try:
                    # Durable outer loop: catches retryable errors and
                    # resumes from the most recent checkpoint.
                    while True:
                        try:
                            raw = await plugins.pattern.execute()
                            self._enforce_duration_budget(request=request, started_at=started_at)
                            validation_exhausted = None

                            finalize_fn = getattr(plugins.pattern, "finalize", None)
                            if finalize_fn is None:
                                # Duck-typed pattern without finalize hook: skip validation.
                                result = raw
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
                                            "expected_schema": (
                                                output_type.model_json_schema() if output_type is not None else {}
                                            ),
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
                            break  # durable outer loop success
                        except RETRYABLE_RUN_ERRORS as exc:
                            if not request.durable:
                                raise
                            durable_blob = session_state.get(_DURABLE_STATE_KEY) or {}
                            checkpoint_id = durable_blob.get("checkpoint_id")
                            if checkpoint_id is None:
                                # No checkpoint written yet — nothing to resume to.
                                raise
                            if resume_attempt >= max_resume:
                                await self._event_bus.emit(
                                    "run.resume_exhausted",
                                    run_id=request.run_id,
                                    attempt_index=resume_attempt + 1,
                                    error_type=type(exc).__name__,
                                    limit=max_resume,
                                )
                                raise
                            resume_attempt += 1
                            await self._event_bus.emit(
                                "run.resume_attempted",
                                run_id=request.run_id,
                                checkpoint_id=checkpoint_id,
                                error_type=type(exc).__name__,
                                attempt_index=resume_attempt,
                            )
                            ckpt = await self._session_manager.load_checkpoint(request.session_id, checkpoint_id)
                            if ckpt is None:
                                raise
                            self._rehydrate_pattern_from_checkpoint(
                                pattern=plugins.pattern,
                                checkpoint=ckpt,
                                usage=usage,
                                artifacts=artifacts,
                            )
                            await self._event_bus.emit(
                                "run.resume_succeeded",
                                run_id=request.run_id,
                                checkpoint_id=checkpoint_id,
                                attempt_index=resume_attempt,
                            )
                finally:
                    if checkpoint_handler is not None:
                        self._event_bus.unsubscribe("tool.succeeded", checkpoint_handler)
                        self._event_bus.unsubscribe("llm.succeeded", checkpoint_handler)

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
                if diag is not None:
                    try:
                        diag.on_run_complete(run_result, None)
                    except Exception:  # noqa: BLE001 - diagnostics must never break the run
                        logger.exception("diagnostics on_run_complete raised; ignored")
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
            if diag is not None:
                try:
                    pattern_ctx_for_snapshot = None
                    if agent_plugins is not None:
                        pattern_ctx_for_snapshot = getattr(agent_plugins.pattern, "context", None)
                    snapshot = diag.capture_error_snapshot(
                        run_id=request.run_id,
                        agent_id=request.agent_id,
                        session_id=request.session_id,
                        exc=wrapped_exc,
                        ctx=pattern_ctx_for_snapshot,
                        usage=usage,
                    )
                    run_result.metadata["error_snapshot"] = {
                        "run_id": snapshot.run_id,
                        "error_type": snapshot.error_type,
                        "error_message": snapshot.error_message,
                        "tool_call_chain": snapshot.tool_call_chain,
                        "last_transcript": snapshot.last_transcript,
                        "captured_at": snapshot.captured_at,
                    }
                    diag.on_run_complete(run_result, snapshot)
                except Exception:  # noqa: BLE001 - diagnostics must never mask the original error
                    logger.exception("diagnostics failure-path hook raised; ignored")
            return run_result
        finally:
            if diag_llm_handler is not None:
                try:
                    self._event_bus.unsubscribe("llm.succeeded", diag_llm_handler)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            if diag_llm_fail_handler is not None:
                try:
                    self._event_bus.unsubscribe("llm.failed", diag_llm_fail_handler)
                except Exception:  # noqa: BLE001
                    pass
            if diag_tool_called_handler is not None:
                try:
                    self._event_bus.unsubscribe("tool.called", diag_tool_called_handler)
                except Exception:  # noqa: BLE001
                    pass

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

    # ------------------------------------------------------------------
    # Durable execution helpers
    # ------------------------------------------------------------------

    def _build_step_checkpoint_handler(
        self,
        *,
        run_id: str,
        session_id: str,
        session_state: dict[str, Any],
        pattern: PatternPlugin,
        usage: RunUsage,
        artifacts: list[Any],
    ):
        """Return an async event handler that persists a checkpoint per step.

        Filters by ``payload['run_id'] == run_id``. Skips events while
        ``pattern.context.scratch['__in_batch__']`` is truthy so batched
        tool calls collapse to a single checkpoint at batch completion.
        """
        counter = {"n": 0}

        async def handler(event: Any) -> None:
            payload = event.payload or {}
            # PatternPlugin.emit injects agent_id + session_id (but not run_id)
            # into every payload. Use session_id to isolate across concurrent
            # runs on different sessions; the handler is also unsubscribed when
            # the owning run ends, so cross-run leakage on the SAME session is
            # bounded by the session lock.
            if payload.get("session_id") and payload.get("session_id") != session_id:
                return
            ctx = getattr(pattern, "context", None)
            scratch = getattr(ctx, "scratch", None) if ctx is not None else None
            if isinstance(scratch, dict) and scratch.get("__in_batch__"):
                return
            counter["n"] += 1
            step_index = counter["n"]
            checkpoint_id = f"{run_id}:step:{step_index}"
            # Flush the durable blob into session_state so the checkpoint
            # snapshot captures the latest usage / artifacts / run_id /
            # step counter alongside the stock transcript.
            try:
                session_state[_DURABLE_STATE_KEY] = {
                    "run_id": run_id,
                    "step_counter": step_index,
                    "checkpoint_id": checkpoint_id,
                    "usage": usage.model_dump(),
                    "artifacts": [a.model_dump() if hasattr(a, "model_dump") else a for a in artifacts],
                }
            except Exception:  # noqa: BLE001 - best-effort only
                logger.exception("failed to serialize __durable__ blob")
            try:
                await self._session_manager.create_checkpoint(session_id, checkpoint_id=checkpoint_id)
            except Exception as exc:  # noqa: BLE001 - checkpoint failure never fails the run
                await self._event_bus.emit(
                    "run.checkpoint_failed",
                    run_id=run_id,
                    checkpoint_id=checkpoint_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return
            transcript = session_state.get("_session_transcript", [])
            await self._event_bus.emit(
                "run.checkpoint_saved",
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                step_index=step_index,
                transcript_length=len(transcript),
            )

        return handler

    async def _list_checkpoint_ids(self, session_id: str) -> list[str]:
        """Best-effort list of checkpoint ids for a session.

        Uses ``list_checkpoints`` when the session backend implements it;
        otherwise peeks the private ``_session_checkpoints`` state key.
        """
        list_fn = getattr(self._session_manager, "list_checkpoints", None)
        if callable(list_fn):
            try:
                result = await list_fn(session_id)
                if isinstance(result, list):
                    return list(result)
            except Exception:  # noqa: BLE001
                pass
        try:
            state = await self._session_manager.get_state(session_id)
            ckpts = state.get("_session_checkpoints", {})
            return list(ckpts.keys()) if isinstance(ckpts, dict) else []
        except Exception:  # noqa: BLE001
            return []

    def _rehydrate_pattern_from_checkpoint(
        self,
        *,
        pattern: PatternPlugin,
        checkpoint: SessionCheckpoint,
        usage: RunUsage,
        artifacts: list[Any],
    ) -> None:
        """Seed pattern.context + usage + artifacts from a loaded checkpoint.

        Mutates ``usage`` and ``artifacts`` in place so the runtime's own
        aggregates stay in sync with the rehydrated state.
        """
        ctx = getattr(pattern, "context", None)
        ckpt_state = dict(checkpoint.state or {})
        transcript = list(ckpt_state.get("_session_transcript", []))
        if checkpoint.transcript_length and checkpoint.transcript_length <= len(transcript):
            transcript = transcript[: checkpoint.transcript_length]
        ckpt_artifacts = list(ckpt_state.get("_session_artifacts", []))
        if checkpoint.artifact_count and checkpoint.artifact_count <= len(ckpt_artifacts):
            ckpt_artifacts = ckpt_artifacts[: checkpoint.artifact_count]

        durable_blob = ckpt_state.get(_DURABLE_STATE_KEY) or {}
        # Rehydrate usage from the durable blob (authoritative) if present.
        blob_usage = durable_blob.get("usage")
        if isinstance(blob_usage, dict):
            try:
                restored = RunUsage.model_validate(blob_usage)
                usage.llm_calls = restored.llm_calls
                usage.tool_calls = restored.tool_calls
                usage.input_tokens = restored.input_tokens
                usage.output_tokens = restored.output_tokens
                usage.total_tokens = restored.total_tokens
                usage.input_tokens_cached = restored.input_tokens_cached
                usage.input_tokens_cache_creation = restored.input_tokens_cache_creation
                usage.cost_usd = restored.cost_usd
                usage.cost_breakdown = dict(restored.cost_breakdown)
            except Exception:  # noqa: BLE001
                logger.exception("failed to rehydrate usage from checkpoint")

        # Rehydrate artifacts from the durable blob (authoritative) if present.
        from openagents.interfaces.runtime import RunArtifact as _RunArtifact

        blob_artifacts = durable_blob.get("artifacts")
        if isinstance(blob_artifacts, list):
            artifacts.clear()
            for item in blob_artifacts:
                try:
                    if isinstance(item, dict):
                        artifacts.append(_RunArtifact.model_validate(item))
                    else:
                        artifacts.append(item)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to rehydrate artifact")

        if ctx is not None:
            # Reset pattern-visible state to the checkpointed snapshot.
            ctx.state = dict(ckpt_state)
            ctx.transcript = transcript
            ctx.artifacts = artifacts
            ctx.usage = usage

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
        mcp_pool: Any,
    ) -> None:
        for tool_id, tool in tools.items():
            preflight = getattr(tool, "preflight", None)
            if not callable(preflight):
                continue
            started = time.perf_counter()
            cached_hit, exc = await self._mcp_coordinator.preflight_with_dedup(mcp_pool, tool, tool_id)
            if exc is not None:
                if isinstance(exc, PermanentToolError):
                    propagate = self._prefix_permanent_tool_error(exc, tool_id=tool_id, request=request)
                    await self._event_bus.emit(
                        "tool.preflight",
                        tool_id=tool_id,
                        result="error",
                        error=str(propagate),
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                    raise propagate from exc
                raise exc
            await self._event_bus.emit(
                "tool.preflight",
                tool_id=tool_id,
                result="cached-ok" if cached_hit else "ok",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def _prefix_permanent_tool_error(
        self,
        exc: PermanentToolError,
        *,
        tool_id: str,
        request: RunRequest,
    ) -> PermanentToolError:
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
            return prefixed
        exc.tool_id = exc.tool_id or tool_id
        exc.with_context(
            agent_id=request.agent_id,
            session_id=request.session_id,
            run_id=request.run_id,
        )
        return exc

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
        await self._mcp_coordinator.close_all()

    async def release_session(self, session_id: str) -> None:
        """Drop the MCP session pool for ``session_id`` (closes shared conns).

        Idempotent. Safe to call even if the session never held an MCP pool.
        """
        await self._mcp_coordinator.release_session(session_id)

    async def invalidate_mcp_pools_for_agents(self, agent_ids: set[str] | None = None) -> None:
        """Drop every MCP session pool — used by ``Runtime.reload``.

        Today we don't trace which tools sit under which session, so a
        config reload that touches any agent drains every pool. Pools
        are cheap to rebuild (next run re-warms); correctness trumps
        retaining possibly-stale shared conns.
        """
        del agent_ids  # reserved for a future fine-grained invalidation pass
        await self._mcp_coordinator.close_all()
