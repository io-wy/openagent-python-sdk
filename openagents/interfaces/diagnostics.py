"""DiagnosticsPlugin seam — error snapshots, LLM metrics, export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunResult, RunUsage


class LLMCallMetrics(BaseModel):
    """Timing and token data for a single LLM call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    ttft_ms: float | None = None
    attempt: int = 1
    error: str | None = None


class ErrorSnapshot(BaseModel):
    """Full error context captured at failure time."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    agent_id: str
    session_id: str
    error_type: str
    error_message: str
    traceback: str
    tool_call_chain: list[dict[str, Any]]
    last_transcript: list[dict[str, Any]]
    usage_at_failure: dict[str, Any]
    state_snapshot: dict[str, Any]
    captured_at: str
    error_code: str = "error.unknown"


class DiagnosticsPlugin:
    """Base diagnostics seam — subscribes to run lifecycle for observability.

    Implementations are process-level singletons. Internal state is keyed
    by ``run_id`` to isolate concurrent runs. ``on_run_complete()`` must
    clean up any per-run data to prevent memory leaks.

    The base class provides a working ``capture_error_snapshot`` that
    degrades gracefully when ``ctx`` is None (exception raised before
    the RunContext was fully constructed).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = dict(config or {})

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        """Accumulate metrics for one LLM call within the given run."""

    def capture_error_snapshot(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
        exc: BaseException,
        ctx: "RunContext | None" = None,
        usage: "RunUsage | None" = None,
        last_n: int = 10,
        redact_keys: list[str] | None = None,
    ) -> ErrorSnapshot:
        """Build and return an ErrorSnapshot.

        When ``ctx`` is None the tool_call_chain and last_transcript
        fields degrade to empty lists.
        """
        import copy
        import traceback as tb
        from datetime import datetime, timezone

        from openagents.observability.redact import redact

        chain: list[dict[str, Any]] = []
        transcript: list[dict[str, Any]] = []
        state: dict[str, Any] = {}

        if ctx is not None:
            scratch_chain = None
            scratch = getattr(ctx, "scratch", None)
            if isinstance(scratch, dict):
                scratch_chain = scratch.get("_diag_tool_chain")
            chain = list(scratch_chain or getattr(ctx, "_diag_tool_chain", []) or [])
            raw_transcript = getattr(ctx, "transcript", []) or []
            transcript = list(raw_transcript[-last_n:])
            raw_state = getattr(ctx, "state", {}) or {}
            state = redact(
                copy.deepcopy(raw_state),
                keys=redact_keys or ["api_key", "token", "secret", "password", "authorization"],
                max_value_length=500,
            )

        usage_dict: dict[str, Any] = {}
        if usage is not None:
            usage_dict = usage.model_dump()

        error_code = getattr(exc, "code", None) or "error.unknown"

        return ErrorSnapshot(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback="".join(tb.format_exception(type(exc), exc, exc.__traceback__)),
            tool_call_chain=chain,
            last_transcript=transcript,
            usage_at_failure=usage_dict,
            state_snapshot=state,
            captured_at=datetime.now(timezone.utc).isoformat(),
            error_code=error_code,
        )

    def on_run_complete(
        self,
        result: "RunResult",
        snapshot: ErrorSnapshot | None,
    ) -> None:
        """Called after every run (success or failure).

        Implementations should: (1) back-fill latency percentiles into
        ``result.usage``, (2) trigger export, (3) clean up per-run data.
        """

    def get_run_metrics(self, run_id: str) -> dict[str, Any]:
        """Return accumulated metrics for a run (for debugging)."""
        return {}
