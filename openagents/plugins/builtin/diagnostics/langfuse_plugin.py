"""LangfuseExporter — send run traces to Langfuse."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)
from openagents.interfaces.runtime import RunResult


class LangfuseExporter(DiagnosticsPlugin):
    """Exports run traces to Langfuse after each run.

    What:
        Accumulates LLMCallMetrics per run, then on ``on_run_complete``
        back-fills latency percentiles into ``result.usage`` and creates
        a Langfuse trace (keyed by ``run_id``) with a child span per
        LLM call. ErrorSnapshots, when present, are embedded in the
        trace metadata.

    Usage:
        ``{"diagnostics": {"type": "langfuse", "config":
        {"public_key": "pk", "secret_key": "sk", "host":
        "https://cloud.langfuse.com"}}}``. Requires the
        ``langfuse`` optional extra.

    Depends on:
        - the ``langfuse`` Python package (optional extra)
        - a reachable Langfuse endpoint
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config)
        cfg = self._config
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "LangfuseExporter requires the 'langfuse' package. "
                "Install with: pip install 'io-openagent-sdk[langfuse]'"
            ) from exc
        self._client = Langfuse(
            public_key=cfg.get("public_key", ""),
            secret_key=cfg.get("secret_key", ""),
            host=cfg.get("host", "https://cloud.langfuse.com"),
        )
        self._per_run: dict[str, list[LLMCallMetrics]] = {}

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self._per_run.setdefault(run_id, []).append(metrics)

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        calls = self._per_run.pop(result.run_id, [])
        if calls:
            latencies = sorted(c.latency_ms for c in calls)
            n = len(latencies)
            result.usage.llm_latency_p50_ms = latencies[n // 2]
            if n >= 2:
                p95_idx = min(int(n * 0.95), n - 1)
                result.usage.llm_latency_p95_ms = latencies[p95_idx]
            else:
                result.usage.llm_latency_p95_ms = latencies[0]
            result.usage.llm_retry_count = sum(1 for c in calls if c.attempt > 1)
            ttft_values = [c.ttft_ms for c in calls if c.ttft_ms is not None]
            if ttft_values:
                result.usage.ttft_ms = ttft_values[0]

        stop_reason = result.stop_reason
        stop_reason_str = (
            stop_reason.value if hasattr(stop_reason, "value") else str(stop_reason) if stop_reason else ""
        )
        metadata: dict[str, Any] = {
            "stop_reason": stop_reason_str,
            "llm_calls": result.usage.llm_calls,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "llm_latency_p50_ms": result.usage.llm_latency_p50_ms,
            "llm_latency_p95_ms": result.usage.llm_latency_p95_ms,
            "llm_retry_count": result.usage.llm_retry_count,
        }
        if snapshot is not None:
            metadata["error_snapshot"] = {
                "error_type": snapshot.error_type,
                "error_code": snapshot.error_code,
                "error_message": snapshot.error_message,
                "tool_call_chain": snapshot.tool_call_chain,
                "captured_at": snapshot.captured_at,
            }

        trace = self._client.trace(id=result.run_id, metadata=metadata)
        for i, call in enumerate(calls):
            trace.span(
                name=f"llm.call.{i}",
                metadata={
                    "model": call.model,
                    "latency_ms": call.latency_ms,
                    "ttft_ms": call.ttft_ms,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cached_tokens": call.cached_tokens,
                    "attempt": call.attempt,
                    "error": call.error,
                },
            )
