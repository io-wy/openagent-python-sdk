"""Test diagnostics plugins — simple capturing implementations for assertions."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)
from openagents.interfaces.runtime import RunResult


class CapturingDiagnosticsPlugin(DiagnosticsPlugin):
    """Diagnostics plugin that records every call for later assertions."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.llm_calls: list[tuple[str, LLMCallMetrics]] = []
        self.run_completes: list[tuple[RunResult, ErrorSnapshot | None]] = []

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self.llm_calls.append((run_id, metrics))

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        self.run_completes.append((result, snapshot))


_singleton_capture: CapturingDiagnosticsPlugin | None = None


class SingletonCapturingDiagnosticsPlugin(CapturingDiagnosticsPlugin):
    """Capturing plugin with a process-wide singleton accessor.

    The plugin loader instantiates fresh per Runtime build. Tests that
    need to assert against the instance the runtime is actually using
    import :func:`get_last_singleton` after constructing the Runtime.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        global _singleton_capture
        _singleton_capture = self


def get_last_singleton() -> CapturingDiagnosticsPlugin | None:
    return _singleton_capture


def reset_singleton() -> None:
    global _singleton_capture
    _singleton_capture = None
