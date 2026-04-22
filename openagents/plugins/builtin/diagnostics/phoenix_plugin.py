"""PhoenixExporter — send run traces to Arize Phoenix via OpenTelemetry."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)
from openagents.interfaces.runtime import RunResult


class PhoenixExporter(DiagnosticsPlugin):
    """Export run traces as OpenTelemetry spans for Arize Phoenix.

    What:
        Builds a proper parent/child OTel trace tree per run: a root
        ``openagents.run`` span with usage attributes, plus one
        ``openagents.llm.call.<i>`` child span per recorded LLM call.
        Unlike ``OtelEventBusBridge`` (which emits flat one-shot spans
        per event), this exporter produces a structured trace that
        Phoenix and other OTel backends can render as a call tree.

    Usage:
        ``{"diagnostics": {"type": "phoenix"}}``. Requires the
        ``phoenix`` optional extra, which brings in ``opentelemetry-api``
        and ``arize-phoenix-otel``. Phoenix is configured via the
        standard OTel tracer provider — point the OTLP exporter at
        Phoenix before constructing this plugin.

    Depends on:
        - the ``opentelemetry-api`` Python package
        - a configured OTel tracer provider (otherwise spans go to a
          default no-op provider)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config)
        try:
            from opentelemetry import trace as _trace  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "PhoenixExporter requires 'opentelemetry-api'. Install with: pip install 'io-openagent-sdk[phoenix]'"
            ) from exc
        self._trace = _trace
        # An explicit tracer_provider in config overrides the OTel global,
        # which is useful for tests and for applications that want to scope
        # spans to a specific backend without touching process-wide state.
        self._tracer_provider = self._config.get("tracer_provider")
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

        # Resolve the tracer lazily so tests can swap the provider.
        if self._tracer_provider is not None:
            tracer = self._tracer_provider.get_tracer("openagents.diagnostics")
        else:
            tracer = self._trace.get_tracer("openagents.diagnostics")
        stop_reason = result.stop_reason
        stop_reason_str = (
            stop_reason.value if hasattr(stop_reason, "value") else str(stop_reason) if stop_reason else ""
        )
        with tracer.start_as_current_span("openagents.run") as root_span:
            root_span.set_attribute("run.id", result.run_id)
            root_span.set_attribute("run.stop_reason", stop_reason_str)
            root_span.set_attribute("run.llm_calls", result.usage.llm_calls)
            root_span.set_attribute("run.input_tokens", result.usage.input_tokens)
            root_span.set_attribute("run.output_tokens", result.usage.output_tokens)
            if result.usage.llm_latency_p50_ms is not None:
                root_span.set_attribute("run.llm_latency_p50_ms", result.usage.llm_latency_p50_ms)
            if result.usage.llm_latency_p95_ms is not None:
                root_span.set_attribute("run.llm_latency_p95_ms", result.usage.llm_latency_p95_ms)

            if snapshot is not None:
                root_span.set_attribute("error.type", snapshot.error_type)
                root_span.set_attribute("error.code", snapshot.error_code)
                root_span.set_attribute("error.message", snapshot.error_message[:500])

            for i, call in enumerate(calls):
                with tracer.start_as_current_span(f"openagents.llm.call.{i}") as span:
                    span.set_attribute("llm.model", call.model)
                    span.set_attribute("llm.latency_ms", call.latency_ms)
                    span.set_attribute("llm.input_tokens", call.input_tokens)
                    span.set_attribute("llm.output_tokens", call.output_tokens)
                    span.set_attribute("llm.cached_tokens", call.cached_tokens)
                    span.set_attribute("llm.attempt", call.attempt)
                    if call.ttft_ms is not None:
                        span.set_attribute("llm.ttft_ms", call.ttft_ms)
                    if call.error is not None:
                        span.set_attribute("llm.error", call.error)
