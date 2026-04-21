"""NullDiagnosticsPlugin — no-op default implementation."""

from __future__ import annotations

from openagents.interfaces.diagnostics import DiagnosticsPlugin


class NullDiagnosticsPlugin(DiagnosticsPlugin):
    """No-op diagnostics plugin used as the default when none is configured.

    What:
        Process-level plugin whose ``record_llm_call``, ``on_run_complete``
        and ``get_run_metrics`` are inherited no-ops. Only
        ``capture_error_snapshot`` performs real work (inherited from
        the base class) so the runtime can always attach error context
        regardless of which backend is active.

    Usage:
        ``{"diagnostics": {"type": "null"}}`` — or simply omit the
        ``diagnostics`` key entirely; the loader substitutes this plugin
        by default.

    Depends on:
        - the base ``DiagnosticsPlugin`` implementation
        - no external services
    """
