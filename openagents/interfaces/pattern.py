"""Pattern plugin contract."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from openagents.errors.exceptions import BudgetExhausted, ModelRetryError
from openagents.interfaces.diagnostics import LLMCallMetrics
from openagents.interfaces.runtime import ErrorDetails

from .plugin import BasePlugin
from .run_context import RunContext
from .tool import ToolExecutionResult

if TYPE_CHECKING:
    from .events import EventBusPlugin
    from .followup import FollowupResolution
    from .response_repair import ResponseRepairDecision
    from .runtime import RunArtifact, RunRequest, RunUsage
    from .session import SessionArtifact
    from .tool import ToolExecutor


ExecutionContext = RunContext[Any]


def unwrap_tool_result(result: Any) -> tuple[Any, dict[str, Any] | None]:
    """Unwrap a tool invocation return.

    Bound tools (via :class:`_BoundTool` in the default runtime) return
    the full :class:`ToolExecutionResult` so executor metadata such as
    retry counts, timeouts, and policy decisions can flow into events.
    Raw :class:`ToolPlugin.invoke` returns whatever the tool itself
    produced, which is treated as opaque data with no metadata.

    Custom patterns that override ``call_tool`` and call
    ``tool.invoke()`` directly should call this helper to handle both
    return shapes uniformly.
    """
    if isinstance(result, ToolExecutionResult):
        return result.data, dict(result.metadata or {})
    return result, None


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

    async def call_tool_batch(
        self,
        requests: list[tuple[str, dict[str, Any]]],
    ) -> list[Any]:
        """Batch-dispatch N tool calls through the bound-tool layer.

        Groups calls by ``tool_id`` so each tool's ``invoke_batch`` can optimize
        (MCP bulk calls, multi-file reads, pipelined HTTP). Results are returned
        in the same order as ``requests``. Emits ``tool.batch.started`` /
        ``tool.batch.completed`` events.
        """
        import time
        from uuid import uuid4

        from .tool import BatchItem

        ctx = self.context
        if ctx is None:
            raise RuntimeError("PatternPlugin.call_tool_batch requires setup() first")

        call_ids: list[str] = [uuid4().hex for _ in requests]
        batch_id = uuid4().hex
        await self.emit(
            "tool.batch.started",
            batch_id=batch_id,
            call_ids=list(call_ids),
            concurrent_count=len(requests),
        )

        groups: dict[str, list[tuple[int, BatchItem]]] = {}
        for idx, (tool_id, params) in enumerate(requests):
            item = BatchItem(params=params or {}, item_id=call_ids[idx])
            groups.setdefault(tool_id, []).append((idx, item))

        results: list[Any] = [None] * len(requests)
        successes = 0
        failures = 0
        started = time.perf_counter()
        ctx.scratch["__in_batch__"] = True
        try:
            for tool_id, pairs in groups.items():
                if tool_id not in ctx.tools:
                    failures += len(pairs)
                    err = KeyError(f"Tool '{tool_id}' is not registered")
                    for idx, _ in pairs:
                        results[idx] = err
                    continue
                tool = ctx.tools[tool_id]
                items = [it for _, it in pairs]
                batch_results = await tool.invoke_batch(items, ctx)
                for (idx, _), br in zip(pairs, batch_results):
                    if br.success:
                        successes += 1
                        results[idx] = br.data
                    else:
                        failures += 1
                        results[idx] = br.exception or RuntimeError(br.error or "batch item failed")
        finally:
            ctx.scratch.pop("__in_batch__", None)
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self.emit(
                "tool.batch.completed",
                batch_id=batch_id,
                successes=successes,
                failures=failures,
                duration_ms=duration_ms,
            )
        return results

    async def call_tool(
        self,
        tool_id: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool with retry and fallback support."""
        from openagents.errors.exceptions import PermanentToolError

        ctx = self.context
        if tool_id not in ctx.tools:
            from openagents.errors.suggestions import near_match

            available = sorted(ctx.tools.keys())
            guess = near_match(tool_id, available)
            extra = f" Did you mean '{guess}'?" if guess else ""
            raise KeyError(f"Tool '{tool_id}' is not registered.{extra} Available tools: {available}")
        tool = ctx.tools[tool_id]
        await self.emit("tool.called", tool_id=tool_id, params=params or {})
        before_tool_calls = ctx.usage.tool_calls if ctx.usage is not None else None
        try:
            result = await tool.invoke(params or {}, ctx)
        except ModelRetryError as retry_exc:
            counts = ctx.scratch.setdefault("__tool_retry_counts__", {})
            counts[tool_id] = counts.get(tool_id, 0) + 1
            budget = ctx.run_request.budget if ctx.run_request is not None else None
            limit = (
                budget.max_validation_retries if budget is not None and budget.max_validation_retries is not None else 3
            )
            if counts[tool_id] > limit:
                await self.emit(
                    "tool.failed",
                    tool_id=tool_id,
                    error=str(retry_exc),
                    error_details=ErrorDetails.from_exception(retry_exc).model_dump(),
                )
                raise PermanentToolError(
                    f"Tool '{tool_id}' exceeded validation retry budget ({limit})",
                    tool_name=tool_id,
                ) from retry_exc
            await self.emit(
                "tool.retry_requested",
                tool_id=tool_id,
                attempt=counts[tool_id],
                error=str(retry_exc),
            )
            ctx.transcript.append(
                {
                    "role": "system",
                    "content": (
                        f"Tool '{tool_id}' requested a retry (attempt {counts[tool_id]}): {retry_exc}. "
                        "Please adjust your arguments and try again."
                    ),
                }
            )
            raise
        except Exception as exc:
            await self.emit(
                "tool.failed",
                tool_id=tool_id,
                error=str(exc),
                error_details=ErrorDetails.from_exception(exc).model_dump(),
            )
            result = await tool.fallback(exc, params or {}, ctx)
            if result is not None:
                return result
            raise

        data, executor_metadata = unwrap_tool_result(result)

        # Successful path resets the retry counter for this tool.
        counts = ctx.scratch.get("__tool_retry_counts__")
        if counts and tool_id in counts:
            counts.pop(tool_id, None)
        ctx.tool_results.append({"tool_id": tool_id, "result": data})
        if ctx.usage is not None and before_tool_calls is not None and ctx.usage.tool_calls == before_tool_calls:
            ctx.usage.tool_calls += 1
        await self.emit(
            "tool.succeeded",
            tool_id=tool_id,
            result=data,
            executor_metadata=executor_metadata,
        )
        return data

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

        # Cost-budget pre-call check (skipped if no budget or cost unavailable).
        budget = ctx.run_request.budget if ctx.run_request is not None else None
        max_cost_usd = budget.max_cost_usd if budget is not None else None
        rate_in = getattr(ctx.llm_client, "price_per_mtok_input", None)
        if (
            max_cost_usd is not None
            and rate_in is not None
            and ctx.usage is not None
            and ctx.usage.cost_usd is not None
        ):
            est_tokens = sum(ctx.llm_client.count_tokens(m.get("content", "") or "") for m in messages)
            projected = ctx.usage.cost_usd + (est_tokens / 1_000_000.0) * rate_in
            if projected > max_cost_usd:
                raise BudgetExhausted(
                    f"cost budget exhausted: projected {projected:.4f} > limit {max_cost_usd:.4f}",
                    kind="cost",
                    current=ctx.usage.cost_usd,
                    limit=max_cost_usd,
                )
        elif max_cost_usd is not None and (rate_in is None or (ctx.usage is not None and ctx.usage.cost_usd is None)):
            if not ctx.scratch.get("__cost_skipped_emitted__"):
                await self.emit(
                    "budget.cost_skipped",
                    limit=max_cost_usd,
                    reason="cost_unavailable",
                )
                ctx.scratch["__cost_skipped_emitted__"] = True

        await self.emit("llm.called", model=model)
        _t_start = time.monotonic()
        try:
            response = await ctx.llm_client.generate(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except BaseException as exc:
            latency_ms = (time.monotonic() - _t_start) * 1000.0
            failure_metrics = LLMCallMetrics(
                model=model or "",
                latency_ms=latency_ms,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                error=str(exc),
            )
            await self.emit(
                "llm.failed",
                model=model,
                _metrics=failure_metrics,
                error_details=ErrorDetails.from_exception(exc).model_dump(),
            )
            raise
        latency_ms = (time.monotonic() - _t_start) * 1000.0

        call_cached_read = 0
        if ctx.usage is not None:
            ctx.usage.llm_calls += 1
            if response.usage is not None:
                ctx.usage.input_tokens += response.usage.input_tokens
                ctx.usage.output_tokens += response.usage.output_tokens
                ctx.usage.total_tokens += response.usage.total_tokens

                meta = response.usage.metadata or {}
                cached_read = int(meta.get("cache_read_input_tokens", meta.get("cached_tokens", 0)) or 0)
                cached_write = int(meta.get("cache_creation_input_tokens", 0) or 0)
                call_cached_read = cached_read
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
        call_metrics = LLMCallMetrics(
            model=model or "",
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens if response.usage is not None else 0,
            output_tokens=response.usage.output_tokens if response.usage is not None else 0,
            cached_tokens=call_cached_read,
            ttft_ms=None,
        )
        await self.emit("usage.updated", usage=ctx.usage.model_dump() if ctx.usage else None)
        await self.emit("llm.succeeded", model=model, _metrics=call_metrics)

        # Cost-budget post-call check.
        if (
            max_cost_usd is not None
            and ctx.usage is not None
            and ctx.usage.cost_usd is not None
            and ctx.usage.cost_usd > max_cost_usd
        ):
            raise BudgetExhausted(
                f"cost budget exhausted: {ctx.usage.cost_usd:.4f} > {max_cost_usd:.4f}",
                kind="cost",
                current=ctx.usage.cost_usd,
                limit=max_cost_usd,
            )
        # If the provider reported no cost mid-run (cost_usd went None),
        # emit budget.cost_skipped exactly once so callers notice.
        if (
            max_cost_usd is not None
            and ctx.usage is not None
            and ctx.usage.cost_usd is None
            and not ctx.scratch.get("__cost_skipped_emitted__")
        ):
            await self.emit(
                "budget.cost_skipped",
                limit=max_cost_usd,
                reason="cost_unavailable",
            )
            ctx.scratch["__cost_skipped_emitted__"] = True

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

    async def finalize(
        self,
        raw: Any,
        output_type: type[BaseModel] | None,
    ) -> Any:
        """Coerce and validate the pattern's raw output.

        Default behavior:
          - output_type is None → return raw unchanged.
          - output_type present → call output_type.model_validate(raw).
        Overriders may pre-process raw before delegating to super().finalize(...).
        """
        if output_type is None:
            return raw
        try:
            return output_type.model_validate(raw)
        except ValidationError as exc:
            raise ModelRetryError(
                self._format_validation_error(exc),
                validation_error=exc,
            )

    async def resolve_followup(self, *, context: "RunContext[Any]") -> "FollowupResolution | None":
        """Override to answer follow-ups locally. Return None to abstain (call LLM)."""
        return None

    async def repair_empty_response(
        self,
        *,
        context: "RunContext[Any]",
        messages: list[dict[str, Any]],
        assistant_content: list[dict[str, Any]],
        stop_reason: str | None,
        retries: int,
    ) -> "ResponseRepairDecision | None":
        """Override to handle bad LLM responses. Return None to abstain (propagate)."""
        return None

    def _format_validation_error(self, exc: "ValidationError") -> str:
        lines = ["The output did not match the expected schema:"]
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", "invalid")
            lines.append(f"- {loc or '(root)'}: {msg}")
        return "\n".join(lines)

    def _inject_validation_correction(self) -> None:
        err = self.context.scratch.pop("last_validation_error", None) if self.context else None
        if err is None:
            return
        self.context.transcript.append(
            {
                "role": "system",
                "content": (
                    f"Your previous final output failed validation "
                    f"(attempt {err['attempt']}): {err['message']}\n"
                    f"Expected schema: {json.dumps(err['expected_schema'], indent=2)}\n"
                    f"Please produce a corrected final output."
                ),
            }
        )
