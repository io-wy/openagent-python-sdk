"""Tool fixtures that exercise the optional ToolPlugin.preflight hook."""

from __future__ import annotations

from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolPlugin

# Module-level state that tests inspect. Fixture classes live in this
# (proper-package) module so `impl=...` import paths resolve to the same
# module instance pytest used to import the test, avoiding the
# implicit-namespace-package pitfall where ``tests.unit.test_runtime_core``
# gets imported twice with divergent module state.

PREFLIGHT_CALLS: list[str] = []


def reset() -> None:
    PREFLIGHT_CALLS.clear()


class FailingPreflightTool(ToolPlugin):
    """Preflight raises PermanentToolError; invoke() must never be reached."""

    name = "failing_preflight_tool"

    def __init__(self, config: dict | None = None):
        super().__init__(config=config or {})

    async def preflight(self, context: Any) -> None:
        raise PermanentToolError(
            "missing external dependency",
            tool_name=self.name,
            hint="install the extra",
        )

    async def invoke(self, params: dict, context: Any) -> Any:  # pragma: no cover
        raise AssertionError("invoke must not run when preflight fails")


class RecordingPreflightTool(ToolPlugin):
    """Preflight appends to PREFLIGHT_CALLS; runs normally otherwise."""

    name = "recording_preflight_tool"

    def __init__(self, config: dict | None = None):
        super().__init__(config=config or {})

    async def preflight(self, context: Any) -> None:
        PREFLIGHT_CALLS.append(self.name)

    async def invoke(self, params: dict, context: Any) -> Any:
        return "ok"


class NoOverrideTool(ToolPlugin):
    """Tool that does not override preflight — base-class no-op must not raise."""

    name = "no_override_tool"

    def __init__(self, config: dict | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict, context: Any) -> Any:
        return "ok"
