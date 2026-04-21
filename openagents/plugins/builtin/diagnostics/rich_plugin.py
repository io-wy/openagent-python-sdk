"""RichDiagnosticsPlugin — local dev panel rendered to stderr."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)
from openagents.interfaces.runtime import RunResult, RunUsage


class RichDiagnosticsPlugin(DiagnosticsPlugin):
    """Render a Rich console panel summarising each agent run.

    What:
        Collects per-run LLMCallMetrics in a dict keyed by ``run_id``.
        When ``on_run_complete`` fires, back-fills latency percentiles
        and retry count into ``result.usage``, then prints a compact
        Rich table on success or a detailed error panel on failure.
        Output goes to stderr so stdout remains reserved for the
        agent's final output.

    Usage:
        ``{"diagnostics": {"type": "rich"}}`` — optional ``config.show``
        is reserved for future filtering. Requires the ``rich`` package.

    Depends on:
        - the ``rich`` Python package (optional extra)
        - no external services
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config=config)
        try:
            from rich.console import Console
        except ImportError as exc:
            raise ImportError(
                "RichDiagnosticsPlugin requires the 'rich' package. Install with: pip install 'io-openagent-sdk[rich]'"
            ) from exc
        self._console = Console(stderr=True)
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

        if snapshot is not None:
            self._render_error_panel(snapshot, result.usage)
        else:
            self._render_success_panel(result.run_id, result.usage)

    def _render_success_panel(self, run_id: str, usage: RunUsage) -> None:
        from rich import box
        from rich.table import Table

        table = Table(title=f"run {run_id}", box=box.SIMPLE, show_header=False)
        table.add_column("key", style="bold cyan")
        table.add_column("value")
        table.add_row("llm_calls", str(usage.llm_calls))
        table.add_row("tokens in/out", f"{usage.input_tokens} / {usage.output_tokens}")
        table.add_row(
            "latency p50",
            f"{usage.llm_latency_p50_ms:.1f}ms" if usage.llm_latency_p50_ms is not None else "n/a",
        )
        table.add_row(
            "latency p95",
            f"{usage.llm_latency_p95_ms:.1f}ms" if usage.llm_latency_p95_ms is not None else "n/a",
        )
        table.add_row("retries", str(usage.llm_retry_count))
        self._console.print(table)

    def _render_error_panel(self, snapshot: ErrorSnapshot, usage: RunUsage) -> None:
        from rich.panel import Panel

        lines = [
            f"[bold red]{snapshot.error_type}[/]: {snapshot.error_message}",
            "",
            f"run_id: {snapshot.run_id}  |  agent: {snapshot.agent_id}  |  session: {snapshot.session_id}",
            "",
            "[bold]Tool call chain:[/]",
        ]
        if snapshot.tool_call_chain:
            for entry in snapshot.tool_call_chain:
                lines.append(f"  -> {entry.get('tool_id', '?')}  params={entry.get('params', {})}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("[bold]Traceback:[/]")
        lines.append(snapshot.traceback or "(unavailable)")
        self._console.print(Panel("\n".join(lines), title="[red]Run Failed[/]", border_style="red"))
