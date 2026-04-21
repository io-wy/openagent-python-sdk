"""NullDiagnosticsPlugin — no-op default implementation."""

from __future__ import annotations

from openagents.interfaces.diagnostics import DiagnosticsPlugin


class NullDiagnosticsPlugin(DiagnosticsPlugin):
    """Process-level diagnostics plugin that does nothing.

    Used as the default when no diagnostics are configured.
    All methods are inherited no-ops from DiagnosticsPlugin, except
    ``capture_error_snapshot`` which builds a real snapshot so the
    runtime can always attach error context regardless of backend.
    """
